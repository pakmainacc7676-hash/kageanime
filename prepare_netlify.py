"""Build the static frontend and proxy its /api requests to the deployed backend."""
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

backend = os.environ.get('BACKEND_URL', '').strip().rstrip('/')
parts = urlsplit(backend)
if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.path not in ('', '/') or parts.query or parts.fragment:
    raise SystemExit('Set BACKEND_URL to the HTTPS origin of your deployed backend, for example https://your-backend.onrender.com (no path).')

out = Path('public')
if out.exists():
    shutil.rmtree(out)
out.mkdir()
for filename in ('index.html', 'watch.html', 'black-torch.webp', 'account.js', 'account.css'):
    shutil.copy2(filename, out / filename)
(out / '_redirects').write_text(
    f'/api/*  {backend}/api/:splat  200!\n/watch  /watch.html  200\n', encoding='utf-8'
)
print('Netlify frontend prepared; API requests will be proxied to', backend)
