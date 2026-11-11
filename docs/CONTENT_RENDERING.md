# Content rendering contracts

This is the maintained reference for source semantics, reader annotations and delivered image references. Reader presentation is separate from the source records used by verification and reconstruction. See [parsing](PARSING.md), [testing](TESTING.md) and [remaining work](../TODO.md).

## Source baseline and evidence

The following baseline records source revisions inspected for the 0.4.0 rendering work; these observations are not claims about later upstream releases. Later focused audits are recorded separately below.

| Source | Revision and relevant paths |
| --- | --- |
| MinerU | [`917b06d0b93660980662c99dd72325695630aacd`](https://github.com/opendatalab/MinerU/tree/917b06d0b93660980662c99dd72325695630aacd): `pyproject.toml`, `mineru/render/markdown.py`, `mineru/parser/task_outputs.py` |
| OvisOCR3 | [`17f255e2c8ae6d18d5d5edb0a474bd4ed39aef90`](https://huggingface.co/ATH-MaaS/OvisOCR3/tree/17f255e2c8ae6d18d5d5edb0a474bd4ed39aef90): `README.md`, `prompts/figure_captioning.txt`, `prompts/document_structured.txt`, `prompts/document_markdown.txt`, `document_crosspage.py` |
| llm-for-zotero | [`e0553224af95e1f568def73bb03e795353c685fc`](https://github.com/yilewang/llm-for-zotero/tree/e0553224af95e1f568def73bb03e795353c685fc): `src/utils/mineruV1Result.ts`, `src/utils/mineruChunking.ts`, `src/services/mineru/mineruCache.ts`, `src/services/pdf/pdfFigureCropCache.ts` |
| DocVortex | **0.6.13**, pinned by this application and within the inspected MinerU requirement `>=0.6.13,<0.7`. MinerU delegates Markdown rendering to `docvortex.render.markdown`. |

The [Ovis model examples](https://huggingface.co/ATH-MaaS/OvisOCR3/blob/17f255e2c8ae6d18d5d5edb0a474bd4ed39aef90/README.md), [technical report](https://arxiv.org/abs/2610.33066), [ATH-MaaS demo](https://huggingface.co/spaces/ATH-MaaS/OvisOCR3/tree/main) (`inference.py`, `pipeline.py`) and [alternate demo](https://huggingface.co/spaces/hugging-apps/ovisocr3/blob/main/app.py) distinguish task contracts from wrapper behavior. Both inspected demo generators use greedy decoding. Their token budgets and timeout/UI controls are wrapper choices, not a caption-specific verbosity control. The 0.4.0 rendering changes left official prompts, response schemas, sampling settings and task contracts unchanged and added no runtime dependency.

The demo links above are navigation links to moving branches; their inspected checkout revisions were not recorded in this reference. They do not establish that current demo code matches the historical observations.

Observed contract decisions:

- Figure captioning requests concise English descriptions of at most 100 words. Retain the complete returned caption; do not shorten it with a custom prompt or force brevity through generation truncation. No caption-specific verbosity control was found in the inspected sources.
- Ovis tables use HTML. Preserve spans, captions, notes and embedded graphics.
- Ovis has no dedicated list category. Keep supplied text Markdown intact. Native unmarked unordered items may become Markdown bullets; existing decimal, alphabetic and Roman markers, nesting, continuation lines and indices must survive. Ellipses and `\cdots` alone are not list evidence.
- Generated Mermaid is interpretation, not source transcription. Omit it from the reader transcript and retain the raw caption response in diagnostics. Legitimate source Mermaid and code remain source content.
- Renaming crops is not inherently a link blocker: ordinary Markdown destinations participate in the server and plugin path rewriting. Source-image cleanup later in the plugin can still remove their targets without retargeting the links.

## Output-budget contract

For 0.4.1, the pinned Ovis README and `document_crosspage.py`, all bundled task prompts, both demo wrappers and the [technical report](https://arxiv.org/abs/2610.33066) were compared for output-budget constraints. The README and cross-page example use `SamplingParams(max_tokens=16384, temperature=0.0)`. The [hugging-apps wrapper at 971a581](https://huggingface.co/spaces/hugging-apps/ovisocr3/blob/971a58106d546add1a230de2dd9936648c600f2b/app.py) explicitly clamps UI requests to 16,384. The [ATH-MaaS wrapper at 72851f1](https://huggingface.co/spaces/ATH-MaaS/OvisOCR3/blob/72851f1a4d49e480527a2dc05c920356c9e3d581/app.py) offers a slider with the same upper bound and forwards the chosen budget through its pipeline/inference functions.

These are example/wrapper policies. No mandatory 16,384-token ceiling was found in the task contracts or report. The application therefore uses the configured output budget consistently across tasks, preserving prompts, response structures and greedy sampling. Existing context validation still applies. Automated request-forwarding checks establish budget handling, not model quality at longer output lengths; live long-response behavior remains unverified.

## Reader presentation

`presentation.py` owns complete annotations, including brackets, separators, asset link syntax and cues. Callers provide semantic intent and unlabelled content. Typical output:

```markdown
[Running header: Lecture 5]
[Running footer: Author]
[Printed page iv]
[Caption] Original caption…
[Footnote] Original note…
[Figure note] Original annotation…
[AI img desc] [view_src_img](images/final.webp) Full official caption. View source image if helpful; Caption may be partial/unreliable.
[Unclear formula; pg 7, approx. lower-right; zoom if possible.]
```

Short furniture and unreadable notices stay inside brackets. Source captions and longer notes follow a role marker. Photographs and diagrams use the same inspection cue, acknowledging that generated descriptions may be incomplete or wrong. Cues require no inference call. Captioning-disabled graphics still have an `[Image]` source-image link as well as the normal asset embed. The link identifies the delivered source graphic; it does not promise that degraded original pixels will be clearer or that a consumer's tools permit direct access.

Titles retain explicit levels or leading Markdown heading levels. Text, content and vertical text retain source Markdown and order. Known code gets an untyped fence if needed; algorithms retain their existing representation. Abstracts, references and asides receive compact labels unless a source heading already makes their role apparent. Formula delimiters and printed numbers remain distinct source information; no equation association or tag is invented. Unclear formulas are plain text without guesses or exact coordinates. Partial readings and rejected candidates remain in metadata/diagnostics.

Ordered internal annotation records preserve native above/below caption and note placement. Unambiguous adjacent visual captions attach with their relative position; ambiguous captions remain standalone. Existing structured `captions` and `footnotes` fields are derived from those records, without exposing internal ordering fields. Generated captions remain marked as generated in structured delivery. Reader labels and cues are excluded from model-facing reconstruction inputs.

Accepted reconstructed tables use typed table-body projections: render the merged body at the first source fragment and suppress only continuation bodies. Original structured fragments, captions, notes, page boundaries and intervening prose remain. This preserves the existing tradeoff that continuation cells appear with the first body. Ambiguous source mappings or new image references reject the merge. Existing registered image references must match the source references exactly. Recursive intermediate rendering of image-bearing pages remains unsupported; independent fragments survive.

## Running furniture

Collapse is a reader-only projection; original text and printed counters remain in structured output. Group membership is recorded in diagnostics rather than exhaustive reader-facing coverage lists.

Before furniture projection, a visual header can be restored to a paragraph title when it matches stable top-band title geometry from at least three other physical pages. Left/top position and height must agree; width may vary with title length. Missing geometry, native/logical pages, list headings and competing top headings prevent promotion. Each restored title remains on its own page, including consecutive repetitions. Original type/category remain in metadata and corrections have diagnostic provenance. This geometry heuristic does not establish general model classification accuracy.

- Explicit headers/footers must be wholly in the matching outer 20% of a physical page. Other candidates are only text/content or recognized standalone counters wholly in the outer 12%, no taller than 4% of the page.
- Exclude titles, captions, footnotes, references, algorithms, tables, formulas and graphics. Require matching edge and literal text or verified counter template, with at most 5% horizontal and 2.5% vertical centre drift.
- Require three occurrences for explicit furniture or verified counters, five for wholly inferred text. Retain genuine furniture once per consecutive run; a gap starts a new run. No global page list is inserted into reader Markdown.
- Counter normalization recognizes explicit Page/p./pg fields, standalone counters and credible N/total forms. Arabic and canonical Roman labels must progress with physical pages under a fixed offset. Totals and surrounding literals remain stable. Resets form separate groups; dates, lecture/chapter numbers, decimals and ambiguous suffixes are not wildcarded.
- Normalize counter spacing, including `N/total` and `N / total`. Confirmed pure Arabic counters matching physical page comments disappear from the reader transcript. Differing printed numbering appears locally as `[Printed page 9]` or `[Printed page iv]`. Compound numbered furniture retains its source wording locally; arbitrary text is not discarded to extract a page label.
- Unique/uncertain explicit furniture is labelled individually. Native logical units do not undergo inferred collapse without reliable physical geometry.

## Image references and failure boundaries

Generated descriptions use ordinary links computed from the current registered asset when rendered. Images embedded inside text, fallback Markdown and tables also receive ordinary source-image links after their block, in encounter order. Existing ordinary links to the same asset prevent redundant additions within that block. Fenced/inline code is excluded. This preserves discoverability when the unchanged plugin strips image embeds. Original model-facing content stays unchanged; Markdown inside table cells and HTML anchor rewriting are not assumed.

Discovery, strict validation and rewriting cover Markdown image embeds, ordinary local image-asset links and HTML images. Ordinary document links and external URLs are excluded. Compression, SVG replacement, native namespacing and final content-hash naming update ordered annotations and table-body projections as well as ordinary block content.

Recognized bbox references in full-page structured text/tables/captions/notes and native visual responses are materialized while their input frame is known. Both Markdown and HTML forms work. Bounds must be finite, normalized and positive-area. A known crop frame maps back to its parent coordinates; native image inference can use its original oriented image directly. Materialized bbox asset names include the page and a UUID; final delivery naming uses the registered asset content hash. Invalid model references become localized unclear-image notices with diagnostics; failures in the required crop rendering/materialization operation propagate. Optional asset improvements and native conversion/caption paths have different boundaries; see [failure and fallback policy](PARSING.md#failure-and-fallback-boundaries). New bbox references from multi-page reconstruction are ambiguous and reject that merge.

Final validation still precedes atomic ZIP replacement. Failed validation/serialization leaves no partial ZIP and preserves any existing publication. Synthetic regressions establish the formerly unmaterialized bbox failure class. **The three previously logged jobs have not been individually reproduced**: their source PDFs or retained raw diagnostics are still required. OOM remediation is outside this release.

## Unchanged-plugin lifecycle and its limit

The expanded integration harness is implemented to exercise the actual unchanged plugin functions, with controlled server responses and an in-memory Gecko filesystem for cleanup. Its execution remains outstanding; the following are implemented assertions and source-informed expectations, not a recorded passing lifecycle run:

1. The controlled server fixture supplies validated, finally named assets and crop links. Separate Python regressions cover materialization, compression and reference transformations; this plugin harness does not run real extraction or compression.
2. V1 normalization rewrites ordinary asset destinations into the plugin image namespace.
3. Cache normalization handles collisions and long path components, rewriting destinations with the same map as assets.
4. Chunk merging namespaces assets and their ordinary links, retaining separate chunk identities.
5. Image-embed stripping removes embeds while ordinary crop links remain. Targets resolve while source images remain cached.
6. A matching, complete PDF figure-cache manifest enables source-image pruning. The integration asserts that links can remain in `full.md` while their original targets disappear, and that no automatic link retargeting occurs. An incomplete cache must not trigger this pruning.

Focused checks of unchanged normalization, chunk merging and embed stripping confirm that PNG/WebP/SVG source-image links from dedicated figures and fallback text survive those operations and resolve while assets remain. They supply decoded ZIP fixtures and do not establish HTTP/ZIP or pruning behavior. The full controlled harness and actual viewing-agent access still need execution with their prerequisites. The controlled harness does not exercise Zotero GUI rendering or actual PDF figure extraction quality. Run the plugin integration with the prerequisites in [TESTING.md](TESTING.md); missing prerequisites must be reported as unverified, not passed.

## Validation scope

Generated temporary artifacts and controlled responses test semantics and the failure class without private document contents. These checks do not establish corpus accuracy or 6 GB CUDA headroom. Real CUDA, the private failing-job reproductions, host LibreOffice/Flatpak and actual Zotero sessions remain user-owned deployment checks. See the [engineering directions](../TODO.md#engineering-directions) for separate fidelity/reliability, Advanced quality/performance, shared efficiency, native Advanced and optional lower-effort tracks.
