import ast,json,subprocess,xml.etree.ElementTree as ET
from pathlib import Path
files=subprocess.check_output(['git','ls-files','-z']).decode().split('\0')
counts={'python':0,'json':0,'keil_xml':0}
for name in files:
    if not name:continue
    p=Path(name)
    assert not (p.name.startswith('.env') and not p.name.endswith('.example')), 'Runtime env file tracked'
    assert p.suffix not in {'.pem','.key','.dump','.bundle'}, 'Credential/backup file tracked'
    assert p.stat().st_size<50*1024*1024, 'Large file requires separate storage'
    if p.suffix=='.json':
        json.loads(p.read_text(encoding='utf-8-sig'));counts['json']+=1
    if p.suffix=='.py' and not any(x in p.parts for x in ['Drivers','MvImport','vanjee_driver','yolo_ros']):
        ast.parse(p.read_text(encoding='utf-8-sig'),filename=name);counts['python']+=1
    if p.suffix=='.uvprojx':
        ET.parse(p);counts['keil_xml']+=1
print(counts)
