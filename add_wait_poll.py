import json

fpath = r'workflows\Reddit Shorts Automation - V2.json'
with open(fpath, 'r', encoding='utf-8') as f:
    wf = json.load(f)

nodes = wf['nodes']
conns = wf['connections']

# 1. Add Wait node (60 seconds)
wait_id = 'wait-after-render-001'
wait_node = {
    "id": wait_id,
    "name": "Esperar Render",
    "type": "n8n-nodes-base.wait",
    "typeVersion": 1.1,
    "position": [1800, 300],
    "parameters": {
        "unit": "seconds",
        "amount": 60
    }
}
nodes.append(wait_node)

# 2. Add Poll job status node
poll_id = 'poll-job-status-001'
poll_node = {
    "id": poll_id,
    "name": "Consultar Estado Job",
    "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.1,
    "position": [2000, 300],
    "parameters": {
        "method": "GET",
        "url": "=http://192.168.1.49:8000/api/jobs/{{ $('Llamada al Engine').item.json.job_id }}",
        "sendHeaders": True,
        "headerParameters": {
            "parameters": [
                {"name": "X-API-Key", "value": "1275"}
            ]
        },
        "options": {}
    }
}
nodes.append(poll_node)

# 3. Update Descargar Video to use video_url from job status
for n in nodes:
    if n['name'] == 'Descargar Video':
        n['position'] = [2200, 300]
        n['parameters']['url'] = "=http://192.168.1.49:8000{{ $json.video_url }}"
        break

# 4. Update connections
# Llamada al Engine -> Esperar Render -> Consultar Estado Job -> Descargar Video
conns['Llamada al Engine'] = {
    "main": [[{"node": "Esperar Render", "type": "main", "index": 0}]]
}
conns['Esperar Render'] = {
    "main": [[{"node": "Consultar Estado Job", "type": "main", "index": 0}]]
}
conns['Consultar Estado Job'] = {
    "main": [[{"node": "Descargar Video", "type": "main", "index": 0}]]
}

with open(fpath, 'w', encoding='utf-8') as f:
    json.dump(wf, f, indent=2)
print('V2 workflow updated with wait + poll nodes')
