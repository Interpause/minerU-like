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
  options: {tier:process.env.TEST_TIER || 'standard', effort:'medium', imageAnalysis:true, serverUrl:baseUrl, apiKey},
  forceOcr:process.env.TEST_FORCE_OCR === '1', report: s => reports.push(s),
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
  assert.ok(result.mdContent.includes(`[crop](${image})`));
  if (process.env.TEST_CROP_LIFECYCLE === '1') await checkCropLifecycle(result);
} else {
  assert.ok(content.length > 0);
  if (process.env.TEST_OUTPUT) writeFileSync(process.env.TEST_OUTPUT, JSON.stringify({service, pageCount: result.pageCount, markdown: result.mdContent, content, files: result.files.map(f => f.relativePath), reports}, null, 2));
}
assert.ok(reports.some(s => s.includes('Downloading')));
if (['flash', 'basic'].includes(process.env.TEST_TIER)) assert.ok(reports.some(s => s.includes("isn't offered")));
console.log(JSON.stringify({service, pageCount:result.pageCount, files:result.files.map(f => f.relativePath), reports}));


async function checkCropLifecycle(input) {
  const cache = await import(pathToFileURL(`${root}/src/services/mineru/mineruCache.ts`).href);
  const chunks = await import(pathToFileURL(`${root}/src/utils/mineruChunking.ts`).href);
  const figures = await import(pathToFileURL(`${root}/src/services/pdf/pdfFigureCropCache.ts`).href);
  const encoder = new TextEncoder();
  // Exercise collision and length normalization on ordinary destinations.
  const long = `images/${'long'.repeat(80)}.png`;
  const collision = cache.normalizeMineruCacheFiles(`[a](images/x.png) [b](auto/images/x.png) [c](${long})`, [
    {relativePath:'images/x.png', data:encoder.encode('a')},
    {relativePath:'auto/images/x.png', data:encoder.encode('b')},
    {relativePath:long, data:encoder.encode('c')},
  ]);
  assert.equal(new Set(collision.files.map(f => f.relativePath)).size, 3);
  for (const ref of collision.mdContent.matchAll(/\]\(([^)]+)\)/g)) {
    assert.ok(collision.files.some(f => f.relativePath === ref[1]));
  }
  const merged = chunks.mergeMineruChunkResults([0,1].map(index => ({
    range:{index,startPage:index+1,endPage:index+1,total:2}, result:input,
  })));
  const finalized = cache.finalizeMineruCacheFiles(merged.mdContent, merged.files, {keepSourceImages:true,pageCount:2});
  assert.ok(!finalized.mdContent.includes('![image]'));
  const links = [...finalized.mdContent.matchAll(/\[crop\]\(([^)]+)\)/g)].map(m => m[1]);
  assert.equal(links.length, 2);
  assert.notEqual(links[0], links[1]);
  for (const link of links) assert.ok(finalized.files.some(f => f.relativePath === link));

  // Gecko IO is supplied by an in-memory filesystem; plugin pruning code is unchanged.
  const itemDir = '/test-cache';
  const files = new Map(finalized.files.map(f => [`${itemDir}/${f.relativePath}`, f.data]));
  files.set(`${itemDir}/full.md`,encoder.encode(finalized.mdContent));
  globalThis.IOUtils = {
    exists: async path => files.has(path),
    write: async (path, data) => { files.set(path, data); },
    makeDirectory: async () => {},
    read: async path => { if (!files.has(path)) throw new Error('missing'); return files.get(path); },
    remove: async path => { for (const key of files.keys()) if (key === path || key.startsWith(`${path}/`)) files.delete(key); },
    getChildren: async path => {
      const prefix = `${path}/`;
      const children = [...new Set([...files.keys()].filter(key => key.startsWith(prefix)).map(key => prefix + key.slice(prefix.length).split('/')[0]))];
      if (!children.length) throw new Error('not a directory');
      return children;
    },
  };
  const manifest = {allFigures:[{label:'Figure 1'}],sections:[],totalPages:2};
  // Incomplete figure caches must keep source images.
  assert.equal(await cache.pruneMineruSourceImagesWhenFigureCropsReady(itemDir, manifest),false);
  for (const link of links) assert.ok(files.has(`${itemDir}/${link}`));
  const cropPath = await figures.writePdfFigureCropBytesToDir(itemDir,'figure-1',encoder.encode('PDF crop'));
  await figures.writePdfFigureCropCacheToDir(itemDir,{
    version:figures.PDF_FIGURE_CROP_CACHE_VERSION,
    algorithmVersion:figures.PDF_FIGURE_CROP_ALGORITHM_VERSION,
    manifestHash:figures.buildPdfFigureCropManifestHash(manifest),
    missingFigures:[],entries:[{label:'Figure 1',cropPath}],
  });
  assert.equal(await cache.pruneMineruSourceImagesWhenFigureCropsReady(itemDir,manifest),true);
  assert.ok(files.has(cropPath));
  const after = new TextDecoder().decode(files.get(`${itemDir}/full.md`));
  assert.equal(after,finalized.mdContent);
  for (const link of links) {
    assert.ok(after.includes(`[crop](${link})`));
    assert.ok(!files.has(`${itemDir}/${link}`));
  }
  assert.ok(!after.includes(`[crop](${cropPath})`));
  delete globalThis.IOUtils;
}
