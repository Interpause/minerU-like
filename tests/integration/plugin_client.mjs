import { registerHooks, createRequire } from 'node:module';
import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL, fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = process.env.TEST_PLUGIN_ROOT;
globalThis.ztoolkit = {getGlobal: name => globalThis[name]};
const ts = createRequire(import.meta.url)(process.env.TYPESCRIPT_MODULE);
registerHooks({
  resolve(specifier, context, next) {
    if (specifier === 'fflate') return {url: pathToFileURL(process.env.FFLATE_MODULE).href, shortCircuit: true};
    if (specifier.endsWith('/package.json')) return {url: new URL(specifier, context.parentURL).href, shortCircuit: true};
    if (specifier.startsWith('.') && !/\.[a-z]+$/i.test(specifier)) return next(specifier + '.ts', context);
    return next(specifier, context);
  },
  load(url, context, next) {
    if (url.endsWith('/package.json')) return {format: 'module', source: `export const config = ${JSON.stringify(JSON.parse(readFileSync(fileURLToPath(url))).config)};`, shortCircuit: true};
    if (url.endsWith('.ts')) return {format: 'module', source: ts.transpileModule(readFileSync(fileURLToPath(url), 'utf8'), {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}}).outputText, shortCircuit: true};
    return next(url, context);
  }
});
const client = await import(pathToFileURL(`${root}/src/utils/mineruLocalClient.ts`).href);
const realPdf = process.env.TEST_REAL_PDF;
client.setMineruV1TimingForTests({pollDelay: () => realPdf ? 1000 : 10});
const baseUrl = process.env.TEST_BASE_URL;
const apiKey = realPdf ? '' : 'integration-key';
const service = await client.detectMineruLocalService(baseUrl, apiKey);
assert.equal(service.api, 'v1');
const reports = [];
const result = await client.parseMineruV1({
  baseUrl, fileName: realPdf ? 'digital.pdf' : 'fixture.pdf', pdfBytes: realPdf ? new Uint8Array(readFileSync(realPdf)) : new TextEncoder().encode('%PDF-test-fixture'), service,
  options: {tier:'standard', effort:'medium', imageAnalysis:true, serverUrl:baseUrl, apiKey},
  forceOcr:false, report: s => reports.push(s),
});
assert.equal(result.pageCount, 1);
if (!realPdf) assert.match(result.mdContent, /Fixture title/);
const content = JSON.parse(new TextDecoder().decode(result.files.find(f => f.relativePath === 'content_list.json').data));
if (!realPdf) {
  assert.equal(content[0].text, 'Fixture title');
  assert.equal(content[0].text_level, 1);
  const image = process.env.TEST_EXPECTED_ASSET || 'images/figure.png';
  assert.equal(content[1].img_path, image);
  assert.ok(result.files.some(f => f.relativePath === image));
} else {
  assert.ok(content.length > 0);
  if (process.env.TEST_OUTPUT) writeFileSync(process.env.TEST_OUTPUT, JSON.stringify({service, pageCount: result.pageCount, markdown: result.mdContent, content, files: result.files.map(f => f.relativePath), reports}, null, 2));
}
assert.ok(reports.some(s => s.includes('Downloading')));
console.log(JSON.stringify({service, pageCount:result.pageCount, files:result.files.map(f => f.relativePath), reports}));
