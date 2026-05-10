import json

fpath = r'workflows\Reddit Shorts Automation - V2.json'
with open(fpath, 'r', encoding='utf-8') as f:
    wf = json.load(f)

new_system_msg = (
    "Eres un director de YouTube Shorts virales. Convierte la historia a español. "
    "NO devuelvas texto normal. Responde EXCLUSIVAMENTE con un JSON valido sin texto adicional. "
    "Divide la historia en entre 4 y 6 escenas cortas (1-2 frases cada una). "
    "REGLA IMPORTANTE: El campo media_filename SIEMPRE debe ser exactamente la palabra: NICHE "
    "(en mayusculas, sin comillas adicionales ni variaciones). "
    "Estructura requerida exacta: "
    "{\"scenes\": [{\"text\": \"Texto de la escena\", \"media_filename\": \"NICHE\", "
    "\"subtitle_pos\": \"center\", \"subtitle_size\": \"large\"}]}"
)

for n in wf['nodes']:
    if n['name'] == 'AI Agent':
        n['parameters']['options']['systemMessage'] = new_system_msg
        print('AI Agent prompt updated')
        break

with open(fpath, 'w', encoding='utf-8') as f:
    json.dump(wf, f, indent=2)
print('V2 JSON saved')
