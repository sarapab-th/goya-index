# Index, Relay, Guardian and GOYA

[Home](../README.md) · [GOYA website](https://goyaos.ai/)

GOYA is being developed as a modular family of AI tools. You should be able to use
a useful component without buying or installing the whole stack.

| Component | Main question | Relationship to this repository |
|---|---|---|
| **Index Harness** | What source is relevant, and do these bytes still match? | This standalone MIT package |
| **GOYA Relay** | Which model/provider should handle this request? | Separate model-gateway product; optional Index context is the integration direction |
| **GOYA Guardian** | Is this exact action authorized within the supported execution boundary? | Separate governance product, not conferred by indexing |
| **GOYA OS** | How do these modules fit into a broader AI workspace? | Product direction, not a replacement OS or complete installer in this repo |

## Relay direction

Relay is the model gateway—not a Wi-Fi router and not just approval notifications.
The direction is **Free, Paid and Enterprise**, with basic Index functionality in
the Free offering and higher tiers serving additional scale, team and administration
needs. Exact pricing and entitlements are outside this repository.

Users choose their own provider connection or connections. Index context is
provider-neutral; multiple providers must remain an explicit routing and
data-sharing choice. This repo does not supply free hosted inference, API credits,
GOYA model weights or automatic Relay wiring. Provider billing remains separate.

The standalone Index code is MIT and does not require a Relay subscription. The
planned Relay bundle does not change this repository's license.

## Downloads and access

- **Index Harness:** get this repository's source and follow [Installation](INSTALLATION.md).
- **GOYA products and access:** [goyaos.ai](https://goyaos.ai/).
- **Product information:** [GOYA product page](https://goyaos.ai/product).

On 9 September 2026 the main site responded, but the checked `/downloads` and
`/relay` routes returned 404. A direct public desktop-download link is therefore
pending. The homepage is not labeled as an installer download. Check the official
site for current access information; this developer preview does not distribute
a desktop installer or a completed GOYA OS.

This project contains no private experiment corpora, private business project,
customer records or other GOYA product source. Historical benchmark summaries
are not a distribution of the private projects used as testbeds.
