"""Small deterministic DESIGN.md -> CSS token drift gate (no YAML dependency)."""
import re
from pathlib import Path
root=Path(__file__).resolve().parents[1]
design=(root/'DESIGN.md').read_text(encoding='utf-8')
css=(root/'src/styles.css').read_text(encoding='utf-8')
colors=design.split('colors:\n',1)[1].split('typography:',1)[0]
errors=[]
for key,value in re.findall(r'^  ([a-z-]+): "([^"]+)"$',colors,re.M):
    if not re.search(r'--color-'+re.escape(key)+r'\s*:\s*'+re.escape(value)+r'\s*[;}]',css):
        errors.append(f'colors.{key} -> --color-{key}: {value} is missing')
if errors:
    raise SystemExit('\n'.join(errors))
print('DESIGN.md color token mappings: OK')
