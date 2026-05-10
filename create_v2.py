import json
import uuid
import os

v1_path = r'c:\Proyectos\editor-n8n\workflows\Reddit Shorts Automation - V1.json'
v2_path = r'c:\Proyectos\editor-n8n\workflows\Reddit Shorts Automation - V2.json'

with open(v1_path, 'r', encoding='utf-8') as f:
    wf = json.load(f)

wf['name'] = 'Reddit Shorts Automation - V2'
# Remove id so n8n creates a new one on import or we can keep it blank for new creation
if 'id' in wf:
    del wf['id']

nodes = wf['nodes']

# Modify AI Agent
for node in nodes:
    if node['name'] == 'AI Agent':
        node['parameters']['text'] = '=Transforma este post en un guion viral en español para vídeo multipantalla: {{ $json.title }}\\n\\n{{ $json.content }}'
        node['parameters']['options']['systemMessage'] = 'Eres un director de YouTube Shorts virales. Convierte la historia a español. NO devuelvas texto normal. Responde EXCLUSIVAMENTE con un JSON válido. Divide la voz en escenas cortas (1-2 frases). Estructura requerida: {"scenes": [{"text": "...", "media_filename": "NICHE", "subtitle_pos": "center", "subtitle_size": "large"}]}'

# Add Parse Code Node
parser_id = str(uuid.uuid4())
parser_node = {
    "parameters": {
        "jsCode": "let aiText = $input.item.json.output || '';\naiText = aiText.replace(/```json/g, '').replace(/```/g, '').trim();\nlet storyboard;\ntry {\n    storyboard = JSON.parse(aiText);\n} catch(e) {\n    storyboard = { scenes: [ { text: aiText.substring(0, 800), media_filename: \"NICHE\", subtitle_pos: \"center\", subtitle_size: \"medium\" } ] };\n}\nreturn storyboard;"
    },
    "id": parser_id,
    "name": "Parse JSON IA",
    "type": "n8n-nodes-base.code",
    "typeVersion": 1,
    "position": [1350, 300]
}
nodes.append(parser_node)

# Modify Engine call
for node in nodes:
    if node['name'] == 'Llamada al Engine':
        node['position'] = [1600, 300]
        node['parameters']['url'] = 'http://192.168.1.49:8000/api/storyboard/render'
        node['parameters']['sendBody'] = True
        node['parameters']['specifyBody'] = 'json'
        # Remove parameter list
        if 'bodyParameters' in node['parameters']:
            del node['parameters']['bodyParameters']
        node['parameters']['jsonBody'] = "={{ \nJSON.stringify({\n  niche: $('Mapeo de Nichos').item.json.niche,\n  voice_id: $('Mapeo de Nichos').item.json.voice_id,\n  music_filename: null,\n  scenes: $json.scenes || []\n})\n}}"

# Update Connections
conns = wf['connections']

# Current connection: AI Agent -> Llamada al Engine
# We want: AI Agent -> Parse JSON IA -> Llamada al Engine

if 'AI Agent' in conns:
    # Change AI Agent output to Parser
    conns['AI Agent']['main'][0] = [{ "node": "Parse JSON IA", "type": "main", "index": 0 }]

# Add Parser output to Llamada al Engine
conns['Parse JSON IA'] = {
    "main": [
        [
            {
                "node": "Llamada al Engine",
                "type": "main",
                "index": 0
            }
        ]
    ]
}

with open(v2_path, 'w', encoding='utf-8') as f:
    json.dump(wf, f, indent=2)

print(f"Workflow V2 created at {v2_path}")
