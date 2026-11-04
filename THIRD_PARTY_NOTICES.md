# Third-party components

The task prompt files under `src/mineru_like/prompts/` are reproduced from [ATH-MaaS/OvisOCR3](https://huggingface.co/ATH-MaaS/OvisOCR3), revision `17f255e2c8ae6d18d5d5edb0a474bd4ed39aef90`, under Apache License 2.0. The accompanying license is in `licenses/OvisOCR3-APACHE-2.0.txt`. New routing and verification instructions are application code rather than upstream benchmark prompts.

OvisOCR3 model weights are separate user-provisioned assets under their upstream license. GGUF quantization files must retain their distributor's model notices. This repository does not redistribute model weights or llama.cpp binaries.

Managed inference targets [llama.cpp build b12110](https://github.com/ggml-org/llama.cpp/releases/tag/b12110), commit `cd959bae9`, licensed MIT. Preserve the runtime distribution's own notices when installing it.

[DocVortex 0.6.13](https://pypi.org/project/docvortex/0.6.13/) declares `MIT AND Apache-2.0 AND BSD-3-Clause`; its wheel contains notices for bundled components. Python dependencies and transitive dependencies retain their individual licenses. The lockfile records the resolved distributions and hashes; review their installed license files for redistribution.

The Zotero compatibility interface was inspected from llm-for-zotero 3.9.16. Plugin implementation files are not distributed by this project; optional integration tests load the user's unchanged checkout.
