import json
import sys
from pathlib import Path

def main():
    data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    required = ('task_id', 'skill_name', 'supplier_id', 'icp_id', 'input_reference', 'approval_required')
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f'Missing fields: {missing}')
    print(json.dumps({'valid': True, 'skill_name': data['skill_name']}))

if __name__ == '__main__':
    main()
