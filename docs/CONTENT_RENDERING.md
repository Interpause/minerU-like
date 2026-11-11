# Content rendering contracts

This is the maintained reference for source semantics, reader annotations and delivered image references. Reader presentation is separate from the source records used by verification and reconstruction. See [parsing](PARSING.md), [testing](TESTING.md) and [remaining work](../TODO.md).

## Source baseline and evidence

The following source revisions were inspected for this change; these observations are not claims about later upstream releases.

| Source | Revision and relevant paths |
| --- | --- |
| MinerU | [`917b06d0b93660980662c99dd72325695630aacd`](https://github.com/opendatalab/MinerU/tree/917b06d0b93660980662c99dd72325695630aacd): `pyproject.toml`, `mineru/render/markdown.py`, `mineru/parser/task_outputs.py` |
| OvisOCR3 | [`17f255e2c8ae6d18d5d5edb0a474bd4ed39aef90`](https://huggingface.co/ATH-MaaS/OvisOCR3/tree/17f255e2c8ae6d18d5d5edb0a474bd4ed39aef90): `README.md`, `prompts/figure_captioning.txt`, `prompts/document_structured.txt`, `prompts/document_markdown.txt`, `document_crosspage.py` |
| llm-for-zotero | [`e0553224af95e1f568def73bb03e795353c685fc`](https://github.com/yilewang/llm-for-zotero/tree/e0553224af95e1f568def73bb03e795353c685fc): `src/utils/mineruV1Result.ts`, `src/utils/mineruChunking.ts`, `src/services/mineru/mineruCache.ts`, `src/services/pdf/pdfFigureCropCache.ts` |
| DocVortex | **0.6.13**, pinned by this application and within the inspected MinerU requirement `>=0.6.13,<0.7`. MinerU delegates Markdown rendering to `docvortex.render.markdown`. |

The [Ovis model examples](https://huggingface.co/ATH-MaaS/OvisOCR3/blob/17f255e2c8ae6d18d5d5edb0a474bd4ed39aef90/README.md), [technical report](https://arxiv.org/abs/2610.33066), [ATH-MaaS demo](https://huggingface.co/spaces/ATH-MaaS/OvisOCR3/tree/main) (`inference.py`, `pipeline.py`) and [alternate demo](https://huggingface.co/spaces/hugging-apps/ovisocr3/blob/main/app.py) distinguish task contracts from wrapper behavior. Both inspected demo generators use greedy decoding. Their token budgets and timeout/UI controls are wrapper choices, not a caption-specific verbosity control. This release changes no official prompt, response schema, sampling setting or task contract and adds no runtime dependency.

Observed contract decisions:

- Figure captioning requests concise English descriptions of at most 100 words. Retain the complete returned caption; do not shorten it with a custom prompt or force brevity through generation truncation. No caption-specific verbosity control was found in the inspected sources.
- Ovis tables use HTML. Preserve spans, captions, notes and embedded graphics.
- Ovis has no dedicated list category. Keep supplied text Markdown intact. Native unmarked unordered items may become Markdown bullets; existing decimal, alphabetic and Roman markers, nesting, continuation lines and indices must survive. Ellipses and `\cdots` alone are not list evidence.
- Generated Mermaid is interpretation, not source transcription. Omit it from the reader transcript and retain the raw caption response in diagnostics. Legitimate source Mermaid and code remain source content.
- Renaming crops is not inherently a link blocker: ordinary Markdown destinations participate in the server and plugin path rewriting. Source-image cleanup later in the plugin can still remove their targets without retargeting the links.

## Reader presentation

`presentation.py` owns complete annotations, including brackets, separators, asset link syntax and cues. Callers provide semantic intent and unlabelled content. Typical output:

```markdown
[Running header; pg 2–8: Lecture 5]
[Running footer; pg 3–10: Author]
[Caption] Original caption…
[Footnote] Original note…
[Figure note] Original annotation…
[AI img desc] [crop](images/final.webp) Full official caption. Inspect exact labels and relationships if needed.
[Unclear formula; pg 7, approx. lower-right; zoom if possible.]
```

Short furniture and unreadable notices stay inside brackets. Source captions and longer notes follow a role marker. Known photographs use a conditional appearance/identity cue; other graphics use the exact-label/relationship cue. Cues require no inference call and do not declare visuals unimportant. Captioning-disabled graphics still have an `[Image]` crop link as well as the normal asset embed.

Titles retain explicit levels or leading Markdown heading levels. Text, content and vertical text retain source Markdown and order. Known code gets an untyped fence if needed; algorithms retain their existing representation. Abstracts, references and asides receive compact labels unless a source heading already makes their role apparent. Formula delimiters and printed numbers remain distinct source information; no equation association or tag is invented. Unclear formulas are plain text without guesses or exact coordinates. Partial readings and rejected candidates remain in metadata/diagnostics.

Ordered internal annotation records preserve native above/below caption and note placement. Unambiguous adjacent visual captions attach with their relative position; ambiguous captions remain standalone. Existing structured `captions` and `footnotes` fields are derived from those records, without exposing internal ordering fields. Generated captions remain marked as generated in structured delivery. Reader labels and cues are excluded from model-facing reconstruction inputs.

Accepted reconstructed tables use typed table-body projections: render the merged body at the first source fragment and suppress only continuation bodies. Original structured fragments, captions, notes, page boundaries and intervening prose remain. This preserves the existing tradeoff that continuation cells appear with the first body. Ambiguous source mappings or new image references reject the merge. Existing registered image references must match the source references exactly. Recursive intermediate rendering of image-bearing pages remains unsupported; independent fragments survive.

## Running furniture

Collapse is a reader-only projection; every original block and printed counter remains in structured output.

- Explicit headers/footers must be wholly in the matching outer 20% of a physical page. Other candidates are only text/content or recognized standalone counters wholly in the outer 12%, no taller than 4% of the page.
- Exclude titles, captions, footnotes, references, algorithms, tables, formulas and graphics. Require matching edge and literal text or verified counter template, with at most 5% horizontal and 2.5% vertical centre drift.
- Require three occurrences for explicit furniture or verified counters, five for wholly inferred text. Odd/even patterns and gaps use exact compact page coverage; missing pages are not implied.
- Counter normalization recognizes explicit Page/p./pg fields, standalone counters and credible N/total forms. Arabic and canonical Roman labels must progress with physical pages under a fixed offset. Totals and surrounding literals remain stable. Resets form separate groups; dates, lecture/chapter numbers, decimals and ambiguous suffixes are not wildcarded.
- Keep the first occurrence with its exact physical coverage. Redundant standalone Arabic counters matching the page comments may disappear from the reader transcript. Other counters retain a compact physical-to-printed mapping, including Roman front matter.
- Unique/uncertain explicit furniture is labelled individually. Native logical units do not undergo inferred collapse without reliable physical geometry.

## Image references and failure boundaries

Generated descriptions use ordinary links computed from the current registered asset when rendered. Embedded table graphics retain valid HTML images and receive ordinary links outside the HTML in image/cell encounter order. Markdown inside table cells and HTML anchor rewriting are not assumed.

Discovery, strict validation and rewriting cover Markdown image embeds, ordinary local image-asset links and HTML images. Ordinary document links and external URLs are excluded. Compression, SVG replacement, native namespacing and final content-hash naming update ordered annotations and table-body projections as well as ordinary block content.

Recognized bbox references in full-page structured text/tables/captions/notes and native visual responses are materialized while their input frame is known. Both Markdown and HTML forms work. Bounds must be finite, normalized and positive-area. A known crop frame maps back to its parent coordinates; native image inference can use its original oriented image directly. Asset identity includes the page and a unique registration ordinal. Invalid model references become localized unclear-image notices with diagnostics; rendering, filesystem and backend failures propagate. New bbox references from multi-page reconstruction are ambiguous and reject that merge.

Final validation still precedes atomic ZIP replacement. Failed validation/serialization leaves no partial ZIP and preserves any existing publication. Synthetic regressions establish the formerly unmaterialized bbox failure class. **The three previously logged jobs have not been individually reproduced**: their source PDFs or retained raw diagnostics are still required. OOM remediation is outside this release.

## Unchanged-plugin lifecycle and its limit

The existing integration harness exercises the actual unchanged plugin functions, with controlled server responses and an in-memory Gecko filesystem for cleanup:

1. Server materialization, compression, final naming and strict validation keep registered links resolvable.
2. V1 normalization rewrites ordinary asset destinations into the plugin image namespace.
3. Cache normalization handles collisions and long path components, rewriting destinations with the same map as assets.
4. Chunk merging namespaces assets and their ordinary links, retaining separate chunk identities.
5. Image-embed stripping removes embeds while ordinary crop links remain. Targets resolve while source images remain cached.
6. A matching, complete PDF figure-cache manifest enables source-image pruning. The integration asserts that links can remain in `full.md` while their original targets disappear, and that no automatic link retargeting occurs. An incomplete cache must not trigger this pruning.

The viewing agent must use its own visual tools when the plugin has removed the source crop. This accepted limitation does not justify modifying the plugin or adding a lifecycle warning to every cue. The controlled harness does not exercise Zotero GUI rendering or actual PDF figure extraction quality. Run the plugin integration with the prerequisites in [TESTING.md](TESTING.md); missing prerequisites must be reported as unverified, not passed.

## Validation scope

Generated temporary artifacts and controlled responses test semantics and the failure class without private document contents. These checks do not establish corpus accuracy or 6 GB CUDA headroom. Real CUDA, the private failing-job reproductions, host LibreOffice/Flatpak and actual Zotero sessions remain user-owned deployment checks. Performance/MinerU optimization exploration remains high priority and deferred.
