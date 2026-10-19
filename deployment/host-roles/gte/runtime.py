import json,runpy,site,sys
from pathlib import Path
root=Path(__file__).resolve().parent
config=json.loads((root/'runtime.json').read_text(encoding='utf-8-sig'))
site.addsitedir(config['site_packages'])
sys.argv=[str(root/'service.py'),'--config',str(root/'service.private.json')]
runpy.run_path(str(root/'service.py'),run_name='__main__')
