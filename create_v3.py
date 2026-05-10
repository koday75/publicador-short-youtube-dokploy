import json
import uuid

def gen_id():
    return str(uuid.uuid4())

def build_v3():
    with open('workflows/Reddit Shorts Automation - V2.json', 'r', encoding='utf-8') as f:
        v2 = json.load(f)

    # We will rebuild the nodes and connections logically
    # AI_Agent ID
    ai_agent = next(n for n in v2['nodes'] if n['name'] == 'AI Agent')
    
    # Update AI Agent System Message to request image_prompt
    sys_msg = "Eres un director de YouTube Shorts virales. Convierte la historia a español. NO devuelvas texto normal. Responde EXCLUSIVAMENTE con un JSON valido sin texto adicional. Divide la historia en entre 4 y 6 escenas cortas (1-2 frases cada una). Estructura requerida exacta: {\"scenes\": [{\"text\": \"Texto de la escena hablado\", \"image_prompt\": \"Descripción visual detallada para generar la imagen de esta escena en inglés, muy visual, estilo fotorealista o cinematográfico, formato 9:16\", \"subtitle_pos\": \"center\", \"subtitle_size\": \"large\"}]}"
    ai_agent['parameters']['options']['systemMessage'] = sys_msg

    parse_ia = next(n for n in v2['nodes'] if n['name'] == 'Parse JSON IA')
    parse_ia['parameters']['jsCode'] = """let aiText = $input.item.json.output || '';
aiText = aiText.replace(/```json/g, '').replace(/```/g, '').trim();
let storyboard;
try {
    storyboard = JSON.parse(aiText);
} catch(e) {
    storyboard = { scenes: [ { text: aiText.substring(0, 800), image_prompt: "abstract background cinematic", subtitle_pos: "center", subtitle_size: "medium" } ] };
}
return storyboard;"""

    # Create new nodes for Batch
    batch_generate = {
        "id": gen_id(),
        "name": "Generar Imagenes Lote",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.1,
        "position": [ 1550, 300 ],
        "parameters": {
            "method": "POST",
            "url": "http://192.168.1.49:8000/api/ai/batch-generate",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ \nJSON.stringify({\n  scenes: $json.scenes.map(s => ({\n    prompt: s.image_prompt,\n    niche: $('Mapeo de Nichos').item.json.niche,\n    model: \"seedream/5-lite-text-to-image\"\n  }))\n})\n}}",
            "sendHeaders": True,
            "headerParameters": { "parameters": [ { "name": "X-API-Key", "value": "1275" } ] }
        }
    }

    wait_batch = {
        "id": gen_id(),
        "name": "Espera Batch",
        "type": "n8n-nodes-base.wait",
        "typeVersion": 1.1,
        "position": [ 1750, 300 ],
        "parameters": { "unit": "seconds", "amount": 20 }
    }

    poll_batch = {
        "id": gen_id(),
        "name": "Consultar Batch",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.1,
        "position": [ 1950, 300 ],
        "parameters": {
            "method": "GET",
            "url": "=http://192.168.1.49:8000/api/ai/batch-status/{{ $('Generar Imagenes Lote').item.json.batch_id }}",
            "sendHeaders": True,
            "headerParameters": { "parameters": [ { "name": "X-API-Key", "value": "1275" } ] },
            "options": {}
        }
    }

    eval_batch = {
        "id": gen_id(),
        "name": "If Batch Terminado",
        "type": "n8n-nodes-base.if",
        "typeVersion": 1,
        "position": [ 2150, 300 ],
        "parameters": {
            "conditions": {
                "string": [
                    {
                        "value1": "={{ $json.status }}",
                        "operation": "in",
                        "value2": "completed,partial,failed"
                    }
                ]
            }
        }
    }

    rebuild_storyboard = {
        "id": gen_id(),
        "name": "Ensamblar Storyboard",
        "type": "n8n-nodes-base.code",
        "typeVersion": 1,
        "position": [ 2350, 200 ],
        "parameters": {
            "jsCode": """const originalStoryboard = $('Parse JSON IA').item.json;
const batchResult = $input.item.json;

// Asignar los archivos a las escenas
if (batchResult.files && batchResult.files.length > 0) {
    for (let i = 0; i < originalStoryboard.scenes.length; i++) {
        // En caso de que haya menos archivos que escenas (fallos), asignamos default.mp4 o reciclamos
        originalStoryboard.scenes[i].media_filename = batchResult.files[i] || "NICHE";
    }
} else {
    for (let i = 0; i < originalStoryboard.scenes.length; i++) {
        originalStoryboard.scenes[i].media_filename = "NICHE";
    }
}
return originalStoryboard;"""
        }
    }

    # Grab the engine render node
    engine_render = next(n for n in v2['nodes'] if n['name'] == 'Llamada al Engine')
    engine_render['position'] = [2550, 200]
    # In engines, $json.scenes now comes from the Rebuild node, but wait, the expression uses $json.scenes. So we just need to route from rebuild to engine.

    # Wait job and poll job
    wait_job = next(n for n in v2['nodes'] if n['name'] == 'Esperar Render')
    wait_job['position'] = [2750, 200]
    poll_job = next(n for n in v2['nodes'] if n['name'] == 'Consultar Estado Job')
    poll_job['position'] = [2950, 200]
    download_vid = next(n for n in v2['nodes'] if n['name'] == 'Descargar Video')
    download_vid['position'] = [3150, 200]

    # Re-wire logic
    # Parse JSON IA -> Generar Imagenes Lote
    # Generar Imagenes Lote -> Espera Batch
    # Espera Batch -> Consultar Batch
    # Consultar Batch -> If Batch
    # If Batch True -> Ensamblar Storyboard
    # If Batch False -> Espera Batch
    # Ensamblar Storyboard -> Llamada al Engine

    conns = v2['connections']
    
    # Remove old connections from Parse JSON IA
    conns['Parse JSON IA'] = { "main": [ [ { "node": "Generar Imagenes Lote", "type": "main", "index": 0 } ] ] }
    conns['Generar Imagenes Lote'] = { "main": [ [ { "node": "Espera Batch", "type": "main", "index": 0 } ] ] }
    conns['Espera Batch'] = { "main": [ [ { "node": "Consultar Batch", "type": "main", "index": 0 } ] ] }
    conns['Consultar Batch'] = { "main": [ [ { "node": "If Batch Terminado", "type": "main", "index": 0 } ] ] }
    
    conns['If Batch Terminado'] = {
        "main": [
            [ { "node": "Ensamblar Storyboard", "type": "main", "index": 0 } ], # True path
            [ { "node": "Espera Batch", "type": "main", "index": 0 } ] # False path
        ]
    }

    conns['Ensamblar Storyboard'] = { "main": [ [ { "node": "Llamada al Engine", "type": "main", "index": 0 } ] ] }
    
    # Add new nodes to list
    existing_names = [n['name'] for n in v2['nodes']]
    # Filter out nodes we are removing/replacing just in case, but we aren't replacing any, just adding.
    nodes = v2['nodes']
    nodes.extend([batch_generate, wait_batch, poll_batch, eval_batch, rebuild_storyboard])
    
    v2['nodes'] = nodes
    v2['name'] = "Reddit Shorts Automation - V3"

    with open('workflows/Reddit Shorts Automation - V3.json', 'w', encoding='utf-8') as f:
        json.dump(v2, f, indent=2)

if __name__ == '__main__':
    build_v3()
    print("V3 workflow created successfully.")
