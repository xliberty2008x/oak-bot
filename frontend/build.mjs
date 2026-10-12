import {build} from 'esbuild';
import {readFileSync, writeFileSync, existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const here = fileURLToPath(new URL('.', import.meta.url));
const vendor = fileURLToPath(new URL('../oak/web/vendor/a2ui/', import.meta.url));
const result = await build({entryPoints: [here + 'a2ui-runtime.js'], absWorkingDir:here,
  bundle: true, format: 'esm', target: 'es2022', minify: true, legalComments: 'eof', metafile:true,
  write: false, banner: {js: '// Oak restricted A2UI adapter; @a2ui/lit@0.13.0 + @a2ui/web_core@0.13.0. See OAK-VENDOR.md.'}});
const bundle = result.outputFiles[0].text;
const lock = JSON.parse(readFileSync(here + 'package-lock.json', 'utf8'));
const names = [...new Set(Object.keys(result.metafile.inputs).flatMap(path => {
  const match = path.match(/^node_modules\/((?:@[^/]+\/)?[^/]+)\//);
  return match ? [match[1]] : [];
}))].sort();
const packages = [], notices = [];
for (const name of names) {
  const root = here + 'node_modules/' + name + '/';
  const manifest = JSON.parse(readFileSync(root + 'package.json', 'utf8'));
  const pin = lock.packages['node_modules/' + name];
  const licenseFile = ['LICENSE','LICENSE.md','LICENSE.txt','license','license.md'].find(file => existsSync(root + file));
  if (!licenseFile) throw new Error('Missing dependency licence: ' + name);
  packages.push({name,version:manifest.version,license:manifest.license,archive:pin.resolved,integrity:pin.integrity});
  notices.push(name + '@' + manifest.version + '\n' + readFileSync(root + licenseFile, 'utf8'));
}
const artifacts = {'runtime.js':bundle, 'packages.json':JSON.stringify({
  bundle_sha256:createHash('sha256').update(bundle).digest('hex'), packages},null,2)+'\n',
  'THIRD-PARTY-LICENSES.txt':notices.join('\n\n' + '='.repeat(72) + '\n\n')+'\n'};
for (const [name,text] of Object.entries(artifacts)) {
  if (process.argv.includes('--check')) {
    if (readFileSync(vendor + name, 'utf8') !== text) throw new Error('A2UI artifact differs: ' + name + '; run npm run build in frontend.');
  } else writeFileSync(vendor + name, text);
}
