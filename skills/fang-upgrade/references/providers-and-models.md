# OpenFang Providers & Models — Operator Reference

**Scope:** OpenFang **v0.6.9** (`/opt/openfang`, tag `v0.6.9`, commit `acf2587`). Every claim below is
grounded in that source tree or verified against the live container `openfang-openfang-1`.

**`docs/providers.md` is badly out of date and contradicts the code in at least a dozen places.**
Where they disagree, this file follows the code and calls the doc out explicitly. See
[Where `docs/providers.md` lies](#13-where-docsprovidersmd-lies).

---

## Table of contents

- [0. TL;DR for this deployment](#0-tldr-for-this-deployment)
- [1. Drivers](#1-drivers)
- [2. The built-in provider registry — 42, not 20 or 43](#2-the-built-in-provider-registry--42-not-20-or-43)
- [3. The static model catalog — 205 models](#3-the-static-model-catalog--205-models)
- [4. Model id resolution](#4-model-id-resolution)
- [5. Configuration](#5-configuration)
- [6. Custom providers](#6-custom-providers)
- [7. Issue #1195 — the `openai/` prefix strip](#7-issue-1195--the-openai-prefix-strip)
- [8. Fallback — two different mechanisms](#8-fallback--two-different-mechanisms)
- [9. Cost tracking](#9-cost-tracking)
- [10. Model routing](#10-model-routing)
- [11. API endpoints](#11-api-endpoints)
- [12. Auth detection](#12-auth-detection)
- [13. Where `docs/providers.md` lies](#13-where-docsprovidersmd-lies)
- [14. Issue status against v0.6.9](#14-issue-status-against-v069)
- [15. Gotchas (all verified)](#15-gotchas-all-verified)
- [16. Verification recipes](#16-verification-recipes)
- [17. Recommended changes for this box](#17-recommended-changes-for-this-box)

## 0. TL;DR for this deployment

| | |
|---|---|
| Provider | `hyperfusion` (**custom** — not a built-in) |
| Model id | `openai/gpt-oss-120b` |
| Base URL | `https://api.hyperfusion.io/v1` |
| Key env var | `HYPERFUSION_API_KEY` (in `/data/secrets.env`) |
| Driver selected | `OpenAIDriver` via the *unknown-provider + base_url* fallthrough (`drivers/mod.rs:552-566`) |
| **Affected by issue #1195?** | **NO.** Verified end-to-end. See [§7](#7-issue-1195--the-openai-prefix-strip). |
| Cost tracking | **Still wrong, differently.** A `Custom` catalog entry now exists in `/data/custom_models.json` with `0.0/0.0` pricing, so every call reports **$0.00** (before it was the fabricated $1.00/$3.00 per M). Put Hyperfusion's real rates in that file. See [§9](#9-cost-tracking). |
| Context window | **131 072**, correct as of the `/data/custom_models.json` entry. Without that entry an uncatalogued model falls back to `DEFAULT_CONTEXT_WINDOW = 200_000` and every tool-result budget is computed 53 % too large. |

The single most important operational rule for this box:

> **Never rename the provider to `openai`, and never set the agent's model while the provider is
> `openai`.** Both mangle `openai/gpt-oss-120b` into `gpt-oss-120b`, which Hyperfusion rejects with
> HTTP 401. The name `hyperfusion` is load-bearing.

---

## 1. Drivers

`docs/providers.md:3` claims "**3 native LLM drivers**". That is false. There are **8 driver
implementations** plus a chaining wrapper, all in `crates/openfang-runtime/src/drivers/`:

| Module | Type | Wire format |
|---|---|---|
| `anthropic.rs` | native | Anthropic Messages API, `POST {base_url}/v1/messages` (`anthropic.rs:246`) |
| `gemini.rs` | native | `POST {base_url}/v1beta/models/{model}:generateContent?key=…` (`gemini.rs:684`) |
| `openai.rs` | universal | `POST {base_url}/chat/completions` (`openai.rs:79`) |
| `bedrock.rs` | native | AWS Bedrock Converse API, Bearer token |
| `vertex.rs` | native | Vertex AI, GCP OAuth / service account |
| `copilot.rs` | hybrid | GitHub Copilot OAuth device flow → OpenAI-compatible completions |
| `claude_code.rs` | subprocess | drives the `claude` CLI over stdio |
| `qwen_code.rs` | subprocess | drives the `qwen` CLI over stdio |
| `fallback.rs` | wrapper | chains other drivers ([§8](#8-fallback--two-different-mechanisms)) |

### 1.1 Provider → driver dispatch

`create_driver()` at `drivers/mod.rs:333`. Order matters — it is a sequence of early returns:

| # | Match | Driver | Notes |
|---|---|---|---|
| 1 | `anthropic` | `AnthropicDriver` | key: cfg → `ANTHROPIC_API_KEY` |
| 2 | `gemini`, `google` | `GeminiDriver` | key: cfg → `GEMINI_API_KEY` → `GOOGLE_API_KEY` |
| 3 | `codex`, `openai-codex` | `OpenAIDriver` | key: cfg → `OPENAI_API_KEY` → Codex CLI credential (`model_catalog.rs:525`) |
| 4 | `claude-code` | `ClaudeCodeDriver` | `base_url` is reused as the **CLI path**, not a URL |
| 5 | `qwen-code` | `QwenCodeDriver` | `base_url` is reused as the **CLI path** |
| 6 | `github-copilot`, `copilot` | `CopilotDriver` | needs persisted OAuth in `$HOME/.openfang` |
| 7 | `azure`, `azure-openai` | `OpenAIDriver::new_azure` | `api-key` header, deployment URL; **`base_url` mandatory** |
| 8 | `vertex-ai`, `vertex`, `google-vertex` | `VertexAIDriver` | `GOOGLE_APPLICATION_CREDENTIALS` / `GOOGLE_CLOUD_PROJECT` |
| 9 | `bedrock` | `BedrockDriver` | `AWS_REGION` / `AWS_DEFAULT_REGION` |
| 10 | `kimi_coding` | **`AnthropicDriver`** | ← easy to miss: an Anthropic-protocol endpoint |
| 11 | anything in `provider_defaults()` | `OpenAIDriver` | `drivers/mod.rs:524` |
| 12 | unknown **with** `base_url` | `OpenAIDriver` | `drivers/mod.rs:552` — **this is our path** |
| 13 | unknown, key set, no `base_url` | error "no base_url configured" | `drivers/mod.rs:577` |
| 14 | otherwise | error "Unknown provider" | `drivers/mod.rs:589` |

So the real driver split is: **Anthropic protocol** = `anthropic` + `kimi_coding`; **Gemini
protocol** = `gemini`/`google`; **everything else that is not a subprocess/cloud-SDK special case** =
`OpenAIDriver`.

### 1.2 Auth headers (`openai.rs:106`)

```
standard : authorization: Bearer {key}
azure    : api-key: {key}
```

If the resolved key is the **empty string**, `apply_auth` attaches **no header at all** and the
request goes out anonymously (`openai.rs:107-109`). For a custom provider this is a silent failure
mode — you get the upstream's 401, not an OpenFang "missing key" error, because
`drivers/mod.rs:557` uses `unwrap_or_default()`.

---

## 2. The built-in provider registry — 42, not 20 or 43

`builtin_providers()` at `model_catalog.rs:572` defines **exactly 42** providers. Verified live:
`GET /api/providers` → `{"total": 42}`.

- `docs/providers.md:3` says 20. Wrong (stale).
- The module doc comment `model_catalog.rs:3` says "130+ builtin models across 28 providers". Also wrong.
- If you were told "43", that is off by one — there are 42. A 43rd only appears if you
  *register* a custom provider ([§6](#6-custom-providers)); `hyperfusion` is **not** registered on
  this box and does **not** appear in `/api/providers`.

| id | display_name | api_key_env | base_url | key_req | models |
|---|---|---|---|---|---|
| `anthropic` | Anthropic | `ANTHROPIC_API_KEY` | `https://api.anthropic.com` | yes | 7 |
| `openai` | OpenAI | `OPENAI_API_KEY` | `https://api.openai.com/v1` | yes | 16 |
| `gemini` | Google Gemini | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com` | yes | 10 |
| `deepseek` | DeepSeek | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1` | yes | 4 |
| `groq` | Groq | `GROQ_API_KEY` | `https://api.groq.com/openai/v1` | yes | 7 |
| `openrouter` | OpenRouter | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | yes | 22 |
| `requesty` | Requesty | `REQUESTY_API_KEY` | `https://router.requesty.ai/v1` | yes | 5 |
| `mistral` | Mistral AI | `MISTRAL_API_KEY` | `https://api.mistral.ai/v1` | yes | 6 |
| `together` | Together AI | `TOGETHER_API_KEY` | `https://api.together.xyz/v1` | yes | 8 |
| `fireworks` | Fireworks AI | `FIREWORKS_API_KEY` | `https://api.fireworks.ai/inference/v1` | yes | 5 |
| `ollama` | Ollama | `OLLAMA_API_KEY` | `http://localhost:11434/v1` | **no** | 6 |
| `vllm` | vLLM | `VLLM_API_KEY` | `http://localhost:8000/v1` | **no** | 1 |
| `lmstudio` | LM Studio | `LMSTUDIO_API_KEY` | `http://localhost:1234/v1` | **no** | 1 |
| `lemonade` | Lemonade | `LEMONADE_API_KEY` | `http://localhost:8888/api/v1` | **no** | 0 |
| `perplexity` | Perplexity AI | `PERPLEXITY_API_KEY` | `https://api.perplexity.ai` | yes | 4 |
| `cohere` | Cohere | `COHERE_API_KEY` | `https://api.cohere.com/v2` | yes | 4 |
| `ai21` | AI21 Labs | `AI21_API_KEY` | `https://api.ai21.com/studio/v1` | yes | 3 |
| `cerebras` | Cerebras | `CEREBRAS_API_KEY` | `https://api.cerebras.ai/v1` | yes | 4 |
| `sambanova` | SambaNova | `SAMBANOVA_API_KEY` | `https://api.sambanova.ai/v1` | yes | 3 |
| `huggingface` | Hugging Face | `HF_API_KEY` | `https://api-inference.huggingface.co/v1` | yes | 3 |
| `xai` | xAI | `XAI_API_KEY` | `https://api.x.ai/v1` | yes | 9 |
| `replicate` | Replicate | `REPLICATE_API_TOKEN` | `https://api.replicate.com/v1` | yes | 3 |
| `github-copilot` | GitHub Copilot | `GITHUB_TOKEN` | `https://api.githubcopilot.com` | yes | 0 |
| `chutes` | Chutes.ai | `CHUTES_API_KEY` | `https://llm.chutes.ai/v1` | yes | 5 |
| `venice` | Venice.ai | `VENICE_API_KEY` | `https://api.venice.ai/api/v1` | yes | 3 |
| `nvidia` | NVIDIA NIM | `NVIDIA_API_KEY` | `https://integrate.api.nvidia.com/v1` | yes | 5 |
| `qwen` | Qwen (Alibaba) | `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | yes | 11 |
| `minimax` | MiniMax | `MINIMAX_API_KEY` | `https://api.minimax.io/v1` | yes | 7 |
| `zhipu` | Zhipu AI (GLM) | `ZHIPU_API_KEY` | `https://open.bigmodel.cn/api/paas/v4` | yes | 6 |
| `zhipu_coding` | Zhipu Coding (CodeGeeX) | `ZHIPU_API_KEY` | `https://open.bigmodel.cn/api/coding/paas/v4` | yes | 1 |
| `zai` | Z.AI | `ZHIPU_API_KEY` | `https://api.z.ai/api/paas/v4` | yes | 0 |
| `zai_coding` | Z.AI Coding | `ZHIPU_API_KEY` | `https://api.z.ai/api/coding/paas/v4` | yes | 2 |
| `moonshot` | Moonshot (Kimi) | `MOONSHOT_API_KEY` | `https://api.moonshot.ai/v1` | yes | 5 |
| `kimi_coding` | Kimi for Code | `KIMI_API_KEY` | `https://api.kimi.com/coding` | yes | 1 |
| `qianfan` | Baidu Qianfan | `QIANFAN_API_KEY` | `https://qianfan.baidubce.com/v2` | yes | 3 |
| `volcengine` | Volcano Engine (Doubao) | `VOLCENGINE_API_KEY` | `https://ark.cn-beijing.volces.com/api/v3` | yes | 4 |
| `volcengine_coding` | Volcano Engine Coding Plan | `VOLCENGINE_API_KEY` | `https://ark.cn-beijing.volces.com/api/coding/v3` | yes | 0 |
| `bedrock` | AWS Bedrock | `AWS_ACCESS_KEY_ID` | `https://bedrock-runtime.us-east-1.amazonaws.com` | yes | 8 |
| `azure` | Azure OpenAI | `AZURE_OPENAI_API_KEY` | *(empty — must be set)* | yes | 4 |
| `codex` | OpenAI Codex | `OPENAI_API_KEY` | `https://api.openai.com/v1` | yes | 3 |
| `claude-code` | Claude Code | *(none)* | *(CLI path)* | **no** | 3 |
| `qwen-code` | Qwen Code | *(none)* | *(CLI path)* | **no** | 3 |

Model counts are the live values from `GET /api/providers`; they are computed at
`model_catalog.rs:41-43` and mutated by discovery/custom-model additions.

### 2.1 Three registries that disagree

There are **three independent provider lists** and they are not in sync. This is a real source of
confusion:

1. `builtin_providers()` (`model_catalog.rs:572`) — 42 ids. Drives `/api/providers`, the dashboard,
   `detect_auth()`, and model-count display. **Purely cosmetic/metadata.**
2. `provider_defaults()` (`drivers/mod.rs:97`) — 38 match arms, **52 accepted names** (aliases
   included). Drives actual `OpenAIDriver` construction. **This is what makes a provider work.**
3. `known_providers()` (`drivers/mod.rs:657`) — 38 names, used for validation/UI hints only.

Differences:

| Situation | Names |
|---|---|
| In catalog, **no** `provider_defaults` arm — and that is **by design**, because each has its own dedicated driver branch | `anthropic` (`drivers/mod.rs:337`), `bedrock` (`:497`), `qwen-code` (`:416`) |
| Previously listed here in error: `claude-code` and `github-copilot` **do** have `provider_defaults` arms (`drivers/mod.rs:214` and `:204`) | — |
| Usable as a provider but **invisible** in `/api/providers` and the catalog | `azure-openai`, `baidu`, `codegeex`, `dashscope`, `doubao`, `glm`, `google`, `kimi`, `kimi2`, `model_studio`, `novita`, `novita-ai`, `nvidia-nim`, `openai-codex`, `z.ai` |
| Missing from `known_providers()` though present in the catalog | `bedrock`, `lemonade`, `requesty`, `volcengine_coding`, `zai_coding` |
| In `known_providers()` but absent from the catalog | `novita` |

**The real asymmetry is `novita`/`novita-ai`**: a `provider_defaults` arm exists at
`drivers/mod.rs:289`, so `provider = "novita"` + `NOVITA_API_KEY` is a fully working provider — but
it has **zero catalog entries**, so it is absent from the 42-entry `/api/providers` list (verified
live) and has no models listed anywhere in the UI.

---

## 3. The static model catalog — 205 models

`builtin_models()` at `model_catalog.rs:1067` returns **205** entries. `docs/providers.md` claims 51
and lists 53 rows; both are stale.

`GET /api/models` reports `total = 205 + custom + discovered`, **not** 205. On this box it currently
returns **206** — the 205 builtins plus the one `custom_models.json` entry
(`openai/gpt-oss-120b`). Do not treat 205 as the live number; treat it as the compiled-in floor.

`ModelCatalogEntry` (`openfang-types/src/model_catalog.rs:129`):

```rust
id, display_name, provider, tier, context_window, max_output_tokens,
input_cost_per_m, output_cost_per_m, supports_tools, supports_vision,
supports_streaming, aliases
```

`ModelTier` (`model_catalog.rs:75`): `Frontier | Smart | Balanced | Fast | Local | Custom`
(`Balanced` is `#[default]`). `Custom` and `Local` are the "user-defined" tiers and get resolution
priority — see [§4](#4-model-id-resolution).

Per-provider model counts are in the table in [§2](#2-the-built-in-provider-registry--42-not-20-or-43).

### 3.1 Prefixed vs. native ids — the thing that causes all the trouble

85 of the 205 ids contain a `/`. They fall into **two distinct classes**:

**Class A — OpenFang-namespaced (prefix must be stripped before sending upstream):**

`openrouter/*` (22), `requesty/*` (5), `cerebras/*` (4), `sambanova/*` (3), `hf/*` (3),
`replicate/*` (3), `bedrock/*` (8), `codex/*` (3), `claude-code/*` (3), `qwen-code/*` (3),
`chutes/*` (5), `azure/*` (4).

e.g. catalog id `openrouter/google/gemini-2.5-flash` → OpenRouter must receive
`google/gemini-2.5-flash`.

**Class B — upstream-native ids that merely happen to contain slashes (must NOT be touched):**

`together` → `meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo`, `Qwen/Qwen2.5-72B-Instruct-Turbo`, …
`fireworks` → `accounts/fireworks/models/…`
`nvidia` → `nvidia/llama-3.1-nemotron-70b-instruct`, `meta/llama-3.1-405b-instruct`, …
`groq` → `meta-llama/llama-4-scout-17b-16e-instruct`

OpenFang distinguishes these **only** by string-comparing the first path segment against the
provider name (`strip_provider_prefix`). That heuristic is exactly what breaks in
[issue #1195](#7-issue-1195--the-openai-prefix-strip).

> Note `hf/` and `cerebras/`/`sambanova/`/`replicate/` prefixes do **not** equal their provider ids
> (`huggingface`, …). `strip_provider_prefix("hf/meta-llama/Llama-3.3-70B-Instruct", "huggingface")`
> is a **no-op** — the `hf/` prefix is sent to HuggingFace verbatim. Same for
> `replicate/…` vs provider `replicate` (that one *does* match and gets stripped). Audit before
> relying on any prefixed catalog id.

---

## 4. Model id resolution

### 4.1 Does an id absent from the catalog still work? **Yes.**

Empirically proven on this box: `openai/gpt-oss-120b` is **not** in the 205-entry catalog, and the
agent runs fine. The catalog is *advisory*. It is consulted only for:

| Use | Code | Behaviour when the model is absent |
|---|---|---|
| Canonicalisation on agent register | `kernel.rs:1665-1676` | skipped entirely |
| Pricing | `crates/openfang-kernel/src/metering.rs:289` | falls back to **$1.00 / $3.00 per M** |
| Context window | `kernel.rs:2925-2929` | `None` → `DEFAULT_CONTEXT_WINDOW = 200_000` (`agent_loop.rs:225,502`) |
| `available` flag in `/api/models` | `model_catalog.rs:293` | model simply isn't listed |
| Router validation | `routing.rs:136` | emits a warning string only |

Nothing in the request path requires catalog membership. The model string is passed to the driver
and serialised straight into the JSON body.

### 4.2 `find_model()` — `model_catalog.rs:131`

Priority (single scan, then fallbacks):

1. user-defined (`Custom`/`Local` tier) + **exact-case** id match → immediate return
2. user-defined + case-insensitive id
3. builtin + exact-case id
4. builtin + case-insensitive id
5. **display_name** case-insensitive match
6. alias table lookup

Steps 1-2 exist because of issue #856 — a custom `Qwen3-30B-A3B` must not be shadowed by a builtin
`qwen3-30b-a3b`. Note step 5: a model can be selected by its *display name*, so
`"Claude Sonnet 4"` resolves. That is a footgun if a display name collides with someone's real id.

### 4.3 `find_model_for_provider()` — `model_catalog.rs:199`

Provider-scoped variant added for issue #833. Tries exact-case → case-insensitive → display-name →
alias, all scoped to the given provider, and **falls back to unscoped `find_model()`** if nothing
matches. Used by `set_agent_model` when an explicit provider is supplied.

### 4.4 Aliases

`builtin_aliases()` at `model_catalog.rs:967` has **68 pairs**; model entries contribute a further
**74** alias strings, merged with `or_insert` at `model_catalog.rs:33-38` (so `builtin_aliases` wins
on collision). Live total: **89** (`GET /api/models/aliases`). `docs/providers.md` says 23 and its
table is largely wrong — e.g. it claims `sonnet → claude-sonnet-4-20250514`; the code
(`model_catalog.rs:969`) maps `sonnet → claude-sonnet-4-6`.

All alias keys are lowercased at build time and lookups lowercase the input
(`model_catalog.rs:1063`, `:302`), so aliases are case-insensitive.

Selected v0.6.9 aliases that differ from the docs:

| Alias | Resolves to |
|---|---|
| `sonnet`, `claude-sonnet` | `claude-sonnet-4-6` |
| `opus`, `claude-opus` | `claude-opus-4-6` |
| `haiku`, `claude-haiku` | `claude-haiku-4-5-20251001` |
| `gpt5` | `gpt-5.2` |
| `gemini-pro` | `gemini-3.1-pro-preview` |
| `gemini-flash` | `gemini-3-flash-preview` |
| `flash` | `gemini-2.5-flash` |
| `grok`, `grok-4` | `grok-4-0709` |
| `kimi` | `kimi-k2` |
| `minimax` | `MiniMax-M2.7` |
| `codex` | `codex/gpt-5.4` |
| `free` | `openrouter/meta-llama/llama-3.3-70b-instruct:free` |
| `openrouter/free-large` | `openrouter/openai/gpt-oss-120b:free` |

`default_model_for_provider()` (`model_catalog.rs:280`) checks the **alias table under the provider
name first**, then the first catalog model for that provider. So `minimax` as a provider resolves
its default model through the `minimax` *alias*.

---

## 5. Configuration

### 5.1 `[default_model]` — `openfang-types/src/config.rs:1681`

```toml
[default_model]
provider = "hyperfusion"                        # free-form string; NOT validated against a list
model = "openai/gpt-oss-120b"                   # opaque, but see strip_provider_prefix
api_key_env = "HYPERFUSION_API_KEY"
base_url = "https://api.hyperfusion.io/v1"
# subprocess_timeout_secs = 300                 # honoured ONLY by provider = "claude-code"
```

Defaults if the section is absent: `anthropic` / `claude-sonnet-4-20250514` /
`ANTHROPIC_API_KEY` / `base_url = None` (`config.rs:1701-1711`).

### 5.2 `[provider_urls]` — **undocumented**, `config.rs:1265`

`HashMap<String, String>`, provider id → base URL. Not mentioned anywhere in `docs/`. Two effects:

- `create_driver` uses it as the `base_url` when `[default_model].base_url` is unset
  (`kernel.rs:690-695`).
- `apply_url_overrides()` (`model_catalog.rs:369`) **registers unknown providers into the catalog**
  and force-marks them `Configured` (`model_catalog.rs:372-378`). This is the only way to make a
  custom provider appear in `/api/providers`.

```toml
[provider_urls]
hyperfusion = "https://api.hyperfusion.io/v1"
ollama      = "http://192.168.1.100:11434/v1"
```

### 5.3 `[provider_api_keys]` — **undocumented**, `config.rs:1271`

Provider id → env var name, overriding the `{PROVIDER_UPPER}_API_KEY` convention.

`resolve_api_key_env()` (`config.rs:1559`) precedence:
1. `[provider_api_keys]` explicit mapping
2. `[auth_profiles]` lowest-`priority` entry
3. convention: `provider.to_uppercase().replace('-', "_") + "_API_KEY"`

So `hyperfusion` → `HYPERFUSION_API_KEY`, `github-copilot` → `GITHUB_COPILOT_API_KEY` (note: **not**
the `GITHUB_TOKEN` the catalog advertises — Copilot uses OAuth instead, so this never bites).

### 5.4 Key resolution at driver-build time

`kernel.rs:678-687` — the key is resolved through `CredentialResolver`, order:
**vault → `{home_dir}/.env` dotenv → process env var**. The env var *name* comes from
`[default_model].api_key_env` if non-empty, else `resolve_api_key_env(provider)`.

For our box the key lives in `/data/secrets.env` as `HYPERFUSION_API_KEY` (loaded into the container
env). Refer to it as `<API_KEY>`; never inline it.

### 5.5 Local-provider URL env overrides (issue #1154)

`local_provider_url_from_env()` (`drivers/mod.rs:52`) — only for `ollama`, `lmstudio`, `vllm`,
`lemonade`. Reads `OLLAMA_HOST` / `OLLAMA_BASE_URL` (and the `LMSTUDIO_`/`VLLM_`/`LEMONADE_`
equivalents), normalises, appends `/v1` when missing. Precedence in `create_driver`
(`drivers/mod.rs:538-545`): explicit `base_url` → env var → hard-coded localhost default.

`apply_local_env_overrides()` (`model_catalog.rs:351`) mirrors this into the dashboard and flips
`auth_status` to `Configured`.

### 5.6 Per-agent model override

`ModelConfig` (`openfang-types/src/agent.rs:373`):

```toml
[model]
provider   = "groq"
model      = "llama-3.3-70b-versatile"   # serde alias: `name` also accepted (agent.rs:377)
max_tokens = 4096
temperature = 0.7
api_key_env = "GROQ_API_KEY"             # Option<String>
base_url    = "https://…"                # Option<String>
```

At agent registration (`kernel.rs:1650-1690`) OpenFang:
1. copies `[default_model].base_url` into `manifest.model.base_url` if the agent left it unset
   (`kernel.rs:1655-1657`);
2. if `find_model(manifest.model.model)` hits **and** the manifest provider is empty/`"default"`/equal
   to the entry's provider → rewrites provider + model to the canonical **stripped** id
   (`kernel.rs:1665-1676`), and fills `api_key_env`;
3. unconditionally runs `strip_provider_prefix(model, provider)` and persists the result
   (`kernel.rs:1686-1689`).

`pinned_model: Option<String>` (`agent.rs:476`) is honoured **only** in
`KernelMode::Stable` (`kernel.rs:2877-2887`) and bypasses routing entirely.

---

## 6. Custom providers

### 6.1 Minimum viable custom provider

A provider name that is not in `provider_defaults()` reaches `drivers/mod.rs:552`. All it needs is a
`base_url`:

```toml
[default_model]
provider    = "hyperfusion"
model       = "openai/gpt-oss-120b"
api_key_env = "HYPERFUSION_API_KEY"
base_url    = "https://api.hyperfusion.io/v1"
```

The key comes from `config.api_key` (resolved from `api_key_env`), else the
`{PROVIDER_UPPER}_API_KEY` convention, else **empty string with no auth header**
(`drivers/mod.rs:557-561`).

Requests go to `{base_url}/chat/completions` with `Authorization: Bearer <key>`.

**Choose the provider name so that it is NOT the first path segment of your model ids.** That is the
whole of the #1195 mitigation.

### 6.2 Making the custom provider visible in `/api/providers`

`[default_model].base_url` alone does **not** register the provider. Verified: `hyperfusion` is
absent from the live `/api/providers` (42 entries, all built-ins). To register it:

```toml
[provider_urls]
hyperfusion = "https://api.hyperfusion.io/v1"
```

or at runtime:

```bash
K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)
curl -X PUT -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  -d '{"base_url":"https://api.hyperfusion.io/v1"}' \
  http://127.0.0.1:4200/api/providers/hyperfusion/url
```

`set_provider_url()` (`model_catalog.rs:320`) appends a `ProviderInfo` with
`api_key_env = HYPERFUSION_API_KEY`, `key_required = true`, then re-runs `detect_auth()`.
`routes.rs:8043` also probes reachability and merges any models the endpoint advertises via
`merge_discovered_models()`.

> **Warning:** `upsert_provider_url()` (`routes.rs:8066`) rewrites the whole `config.toml` through
> `toml::to_string_pretty` — **comments and formatting are destroyed**, and unlike
> `set_provider_key` it takes **no `.bak` backup**.

### 6.3 Custom models — the catalog *can* be extended

**There is no update path — delete and re-add.** `POST /api/models/custom` on an existing
`(id, provider)` pair returns **409** `Model 'X' already exists for provider 'Y'` and changes
nothing; it is easy to read that as "saved" and move on with stale numbers still in the catalog.
The delete route is `DELETE /api/models/custom/{*id}` — a **wildcard** path, so a slashed model id
goes in literally (`/api/models/custom/kimi/k3`); url-encoding the slash gives a 404. Both calls
apply to the live catalog immediately and persist to `$OPENFANG_HOME/custom_models.json` with no
restart. Editing that file by hand works too but only takes effect after `docker restart`.

Verified 2026-08-10: DELETE then POST on `kimi/k3` changed `max_output_tokens` 16384 -> 32768,
visible in `/api/models` on the next request.

**A router that publishes no limits leaves you guessing.** `y7router`'s `/v1/models` returns only
`id`, `object`, `created`, `owned_by` — no `context_length`, no `max_output_tokens`, no
`supported_parameters`. Whatever you put in the catalog entry is a guess unless you measure it.
Measuring is cheap: binary-search the input size until the provider errors, and place a unique
string at the start, middle and end to tell a real context window from silent truncation. Doing
that to `kimi/k3` found a genuine ~1M-token window where the placeholder said 131072.

**Worse, a router may ignore the parameters it accepts.** Every OpenAI knob sent to `y7router`
(`max_tokens`, `stop`, `seed`, `n`, `logprobs`, `response_format`) returns 200 and is silently
dropped — `max_tokens: 5` produced 419 completion tokens. Nothing in the response says so. If a
model must respect an output cap or return strict JSON, prove it once with a deliberate test
rather than trusting the 200.


Two equivalent routes; both end at `add_custom_model()` (`model_catalog.rs:443`), which forces
`tier = Custom`.

**File:** `$OPENFANG_HOME/custom_models.json` (here `/data/custom_models.json`), loaded at boot
(`kernel.rs:870`). There is **no `config.toml` section for custom models** — grep confirms none.

```json
[
  {
    "id": "openai/gpt-oss-120b",
    "display_name": "GPT-OSS 120B (Hyperfusion)",
    "provider": "hyperfusion",
    "tier": "custom",
    "context_window": 131072,
    "max_output_tokens": 32768,
    "input_cost_per_m": 0.1,
    "output_cost_per_m": 0.5,
    "supports_tools": true,
    "supports_vision": false,
    "supports_streaming": true,
    "aliases": []
  }
]
```

**API:**

Example shape below — **not** the current file contents. The live `custom_models.json` already holds
this `(id, provider)` pair with `0.0/0.0` pricing (§9.2), so re-running this exact POST returns
`409 Conflict`; `DELETE /api/models/custom/openai/gpt-oss-120b` first if you mean to change it.

```bash
# add — 201 Created, writes/rewrites $OPENFANG_HOME/custom_models.json
curl -X POST -H "Authorization: Bearer $K" -H 'Content-Type: application/json' -d '{
  "id":"openai/gpt-oss-120b","provider":"hyperfusion",
  "display_name":"GPT-OSS 120B (Hyperfusion)",
  "context_window":131072,"max_output_tokens":32768,
  "input_cost_per_m":0.1,"output_cost_per_m":0.5,"supports_tools":true
}' http://127.0.0.1:4200/api/models/custom

# remove — raw slashes are fine (wildcard route), %2F also decodes correctly
curl -X DELETE -H "Authorization: Bearer $K" \
  http://127.0.0.1:4200/api/models/custom/openai/gpt-oss-120b
```

Field defaults in `routes.rs:6561-6624` when omitted: `provider` → **`"openrouter"`** (!),
`context_window` → 128000, `max_output_tokens` → 8192, both costs → **0.0**, `supports_tools` → true,
`supports_vision` → false, `supports_streaming` → true. Always pass `provider` explicitly.

Duplicate `(id, provider)` case-insensitively → `409 Conflict` (`model_catalog.rs:446-452`).
`remove_custom_model` only deletes `Custom`-tier entries, so builtins are safe
(`model_catalog.rs:473`, `crates/openfang-runtime/src/model_catalog.rs`).

**`remove_custom_model` recomputes `model_count` — fixed this fork (FANG-61).** Removing a custom
model used to leave the owning provider's `ProviderInfo.model_count` (as returned by
`GET /api/providers`) one too high until the daemon restarted, because only `add_custom_model`
recomputed the count (`model_catalog.rs:458-465`) and `remove_custom_model` just retained/filtered
`self.models` with no matching update. Now `remove_custom_model` collects the `provider`(s) that
actually lost a model *before* retaining, then recomputes `model_count` for each affected provider
by recounting `self.models` after the retain, mirroring `add_custom_model`'s own logic
(`model_catalog.rs:476-494`). The pre-fix bug was a live-daemon-only staleness window, not
permanent corruption: `ModelCatalog::new()` sets every provider's `model_count` from the builtin
models alone at boot (`model_catalog.rs:41-43`, runs *before* any custom model is loaded), and the
subsequent `load_custom_models()` call (`kernel.rs:870`) then re-adds each surviving entry from
`custom_models.json` one at a time through `add_custom_model()` — which recomputes correctly on
every call. A model deleted via the API is no longer in that file, so it simply isn't re-added, and
the count comes out right on the next restart even without the fix. The fix only matters for a
long-running daemon reading its own `/api/providers` between a `DELETE
/api/models/custom/{*id}` call and its next restart.

**This is the supported fix for cost tracking on a custom provider** — see [§9](#9-cost-tracking).

> **`POST /api/models/custom` does NOT register the provider.** Verified live: after adding a custom
> model on provider `hyperfusion`, `GET /api/providers` still returned 42 entries with no
> `hyperfusion`. Only `PUT /api/providers/{name}/url` pushes a synthetic `ProviderInfo`
> (`model_catalog.rs:325-336`). So the documented "add a custom model" flow leaves the provider
> invisible to the dashboard, to the `available` flags, and to `POST /api/providers/{name}/test`.
> Do both calls, not just the model one.

### 6.4 Local model auto-discovery

`merge_discovered_models()` (`model_catalog.rs:391`) adds ids the local server reports, with
`Local` tier and zero cost, and updates `model_count`.

> **Correction:** nothing is discovered on this box. The six Ollama ids you see in `/api/models`
> (`llama3.2`, `llama3.1`, `mistral:latest`, `qwen2.5`, `phi3`, `deepseek-r1:latest`) are **hardcoded
> `builtin_models()` entries** with `provider: "ollama"` — verified by parsing
> `model_catalog.rs`, and by the fact that `total` is exactly `205 + 1 custom` with no Ollama server
> reachable from the container. They show `available: true` only because `ollama` has
> `key_required = false`, so its `auth_status` is `NotRequired`, never `Missing`.

---

## 7. Issue #1195 — the `openai/` prefix strip

**Verdict for this deployment: NOT AFFECTED. Verified end-to-end, twice.**

### 7.1 The mechanism

The one and only place a model id gets rewritten is `strip_provider_prefix`
(`crates/openfang-runtime/src/agent_loop.rs:212`):

```rust
pub fn strip_provider_prefix(model: &str, provider: &str) -> String {
    let slash_prefix = format!("{}/", provider);
    let colon_prefix = format!("{}:", provider);
    if model.starts_with(&slash_prefix) {
        model[slash_prefix.len()..].to_string()
    } else if model.starts_with(&colon_prefix) {
        model[colon_prefix.len()..].to_string()
    } else {
        model.to_string()
    }
}
```

It strips `"{provider}/"` **or** `"{provider}:"` from the front. Nothing else in the codebase
mutates a model id — verified by grepping for `strip_prefix`, `split_once('/')`, `splitn`,
`trim_start_matches` across `crates/`. The only other `strip_prefix` calls on models are
`vertex.rs:182` (`models/`), `claude_code.rs:199` (`claude-code/`), `qwen_code.rs:140`
(`qwen-code/`), and `openai_compat.rs:164` (`openfang:`, inbound server side).

The `OpenAIDriver` itself is innocent: `openai.rs:589` (non-streaming) and `openai.rs:1015`
(streaming) both do

```rust
model: request.model.clone(),
```

— verbatim, no transformation, straight into the serialised `OaiRequest` body (`openai.rs:120-121`).

**So the mangling always happens upstream of the driver, in the kernel/agent-loop, and only when
`model.starts_with(provider + "/")`.**

### 7.2 Why the reporter hit it

Their config was:

```toml
provider = "openai"                 # ← first path segment of the model id
model    = "openai/gpt-oss-120b"
base_url = "https://api.featherless.ai/v1"
```

`strip_provider_prefix("openai/gpt-oss-120b", "openai")` → `"gpt-oss-120b"`. Featherless 404s.
Their own workaround (`zai-org/GLM-5.1`) works precisely because `"zai-org/" != "openai/"`. The code
explains the report exactly, including the workaround. **Issue #1195 is real, correctly diagnosed,
and unfixed in v0.6.9.**

Call sites that would have mangled it:
- `agent_loop.rs:528` / `agent_loop.rs:1765` — every request, non-streaming and streaming
- `kernel.rs:1686` — persisted at agent registration
- `kernel.rs:3367-3372` — persisted on model switch
- `kernel.rs:783`, `kernel.rs:5743`, `kernel.rs:5779` — global fallback chain entries

### 7.3 Why we are safe

Our provider is `hyperfusion`, so `slash_prefix = "hyperfusion/"` and `colon_prefix =
"hyperfusion:"`. `"openai/gpt-oss-120b"` starts with neither → returned unchanged.

Evidence chain:

1. **Persisted manifest** (MessagePack blob in `agents` table of
   `/data/data/openfang.db`) contains `provider␁hyperfusion␁model␁openai/gpt-oss-120b` and
   `base_url␁https://api.hyperfusion.io/v1` — prefix intact after all normalisation passes.
2. **CLI view:** `docker exec openfang-openfang-1 openfang agent list` →
   `assistant  Running  hyperfusion  openai/gpt-oss-120b`.
3. **Live agent call succeeds:**
   ```
   POST /api/agents/17ffd1ca-…/message  {"message":"Reply with exactly: PONG"}
   → {"cost_usd":0.008384,"input_tokens":8306,"output_tokens":26,"response":"PONG"}
   ```
   (Captured before the custom catalog entry existed, which is why `cost_usd` is present at all —
   the same call today returns `{"input_tokens":8529,"iterations":1,"output_tokens":46,
   "response":"PONG"}` with no `cost_usd` key. The 200 is what matters here, not the price.)
4. **Discriminating upstream probe** — the decisive test. Hitting Hyperfusion directly:

   | model sent | result |
   |---|---|
   | `openai/gpt-oss-120b` | **HTTP 200** |
   | `gpt-oss-120b` (what a strip would produce) | **HTTP 401** `key_model_access_denied — "Tried to access gpt-oss-120b"` |

   Since the agent call returns 200, the un-stripped id must be what left the process. There is no
   ambiguity: a stripped id cannot produce a 200 on this account.

### 7.4 How you could still trip over it here

Ranked by likelihood:

1. **Renaming the provider to `openai`** in `[default_model]` (e.g. "it's OpenAI-compatible, so let's
   call it openai") → instant breakage, and `kernel.rs:1686` **persists** the stripped id.
2. **`set_agent_model` with an explicit `openai` provider** (`routes.rs:6085`, `routes.rs:7376`,
   `routes.rs:9883`) → `strip_provider_prefix` at `kernel.rs:3367` writes `gpt-oss-120b` to the
   registry permanently.
3. **`set_agent_model` with provider auto-detect** — `infer_provider_from_model()`
   (`kernel.rs:6876-6901`, `openai` arm at `:6895`) maps a leading `openai/` to provider `"openai"`,
   which then triggers the strip.
   **Be precise about why we are shielded — it is the provider name, not `base_url`.**
   `strip_provider_prefix` runs on **every** request at `agent_loop.rs:528` and `:1765` regardless of
   `base_url`; it is a no-op for us only because `"openai/gpt-oss-120b"` does not start with
   `"hyperfusion/"`. The `has_custom_url` guard at `kernel.rs:3347-3356` is narrower than it looks:
   it only suppresses provider **auto-detection** inside the model-switch path
   (`POST /api/agents/{id}/model`). So removing `base_url` alone changes nothing. To actually break
   it you must *also* switch the model to something with no catalog entry, at which point
   `infer_provider_from_model` rewrites the provider to `openai` and only then does the strip bite.
   - The CLI `openfang agent set <ID> model <VALUE>` has **no `--provider` flag**, so it always uses
     the auto-detect path (guarded). The REST/WS paths can pass an explicit provider.
4. **Registering a custom catalog model under provider `openai`** — `kernel.rs:1669` would then
   canonicalise through `strip_provider_prefix(entry.id, "openai")`.
5. **A colon-prefixed id** — the same function also strips `"{provider}:"`. Harmless for us, but note
   that a model literally named `hyperfusion:something` would be truncated.

### 7.5 A correct fix (for reference / upstream patch)

`strip_provider_prefix` should only strip when the resulting id is still a known catalog id for that
provider, or should be restricted to the Class-A namespaced prefixes, rather than blindly matching
`provider + "/"`. Passing model ids as opaque strings whenever `base_url` is user-supplied — which is
what the reporter asks for — is the minimal correct behaviour.

---

## 8. Fallback — two different mechanisms

Do not confuse them. They have different config keys, different scopes, and different triggers.

### 8.1 Global `[[fallback_providers]]` — `config.rs:473`

```toml
[[fallback_providers]]
provider = "ollama"
model    = "llama3.2:latest"
api_key_env = ""
# base_url = "http://localhost:11434/v1"
# subprocess_timeout_secs = 300
```

Built at `kernel.rs:748-790` into a `FallbackDriver` chain. Model names **are** run through
`strip_provider_prefix` (`kernel.rs:783`). The primary entry carries an empty model string, which
means "use the request's model as-is" (`fallback.rs:42-45`).

**Trigger behaviour — the docs are wrong.** `docs/providers.md:818-823` claims rate-limit/overload
errors "bubble up for retry logic (does NOT failover)". The code (`fallback.rs:46-66`) treats
`RateLimited` and `Overloaded` **exactly like every other error**: log a warning, advance to the next
driver. Only when the chain is exhausted does the last error surface.

**`fallback.rs` contradicts itself in its own doc comments** — read it top-down and you get the wrong
answer. The module header (lines 3-4) says *"If the primary driver fails with a **non-retryable**
error, the fallback driver moves to the next driver"*, while the struct doc (line 13) says *"On
failure (**including rate-limit and overload**), moves to the next driver."* The code matches line 13.

### 8.2 Per-agent `fallback_models` — `agent.rs:443`

An array of **structs**, not strings. `docs/providers.md:836` shows
`fallback_models = ["gemini-2.5-flash", …]` — that is wrong for the manifest type
(`Vec<FallbackModel>`, `agent.rs:407`), though `serde_compat::vec_lenient` may swallow malformed
input silently.

```toml
[[fallback_models]]
provider = "groq"
model    = "llama-3.3-70b-versatile"
# api_key_env = "GROQ_API_KEY"
# base_url = "https://…"
```

Triggered **only** when the primary returns `LlmErrorCategory::ModelNotFound`
(`agent_loop.rs:1253`, `agent_loop.rs:1437`; added for issue #845). Not a general failover.

**Important asymmetry:** `agent_loop.rs:1285` sets `fb_request.model = fb.model.clone()` with **no**
`strip_provider_prefix`. Per-agent fallback model ids are passed through verbatim, so you must write
the exact upstream id. The global chain strips; the per-agent chain does not.

### 8.3 Fallback `base_url` resolution — fixed this fork (FANG-61)

**On stock OpenFang** (and in any deployment still running it — the symptom below is for that
case): a per-agent `[[fallback_models]]` entry with no explicit `base_url` inherited
`[default_model].base_url` unconditionally. If the fallback's provider differed from the default
provider, this posted the *fallback's* API key to the *default's* host — observed as an HTTP 500
wrapping the other provider's 401 body (e.g. a `y7router` key sent to `api.hyperfusion.io`). It
looked intermittent because it only bit when the fallback provider actually differed from the
default one, and `[provider_urls]` was checked, but third — behind `dm.base_url` — so it was only
ever reached when `[default_model]` had *no* `base_url` set at all. The workaround on stock is to
always set an explicit `base_url` on every `[[fallback_models]]` entry whose provider differs from
`[default_model].provider`.

**In this fork, no workaround is needed** — the resolution order was inverted to match how the
*primary* model already worked (`resolve_driver`'s own comment: "Don't inherit default provider's
base_url when switching providers") and how the global `[[fallback_providers]]` chain already
worked (`kernel.rs:768-771`, unchanged by this fix — it always went straight from `fb.base_url` to
`config.provider_urls.get(&fb.provider)`, no `dm.base_url` step, so it was never affected). Only the
per-agent `[[fallback_models]]` path was wrong, and only it needed fixing:

```rust
fn effective_fallback_base_url(
    fb_base_url: Option<&str>,
    fb_provider: &str,
    default_provider: &str,
    default_base_url: Option<&str>,
    lookup_provider_url: impl Fn(&str) -> Option<String>,
) -> Option<String> {
    let inherited = if fb_provider == default_provider { default_base_url } else { None };
    fb_base_url.or(inherited).map(str::to_string).or_else(|| lookup_provider_url(fb_provider))
}
```
(`kernel.rs:6930-6946`). The default's `base_url` is now inherited **only when the fallback's
provider is the same as the default's** — otherwise the fallback's own explicit `base_url`, then
`lookup_provider_url(fb_provider)` (`kernel.rs:5668-5682`: checks boot-time `config.provider_urls`
first, the runtime model catalog second) resolves it. This is applied in two places that both need
to agree, and did not before:

1. `resolve_driver`'s own `ModelNotFound` fallback-driver construction (the `fb_config` at
   `kernel.rs:5883`, now calling `effective_fallback_base_url(...)` instead of the old
   `.or_else(|| dm.base_url.clone())` chain).
2. `apply_fallback_base_urls(manifest)` (`kernel.rs:5692-5708`) — bakes the resolved `base_url`
   into a **copy** of the manifest before it reaches `agent_loop`, because `agent_loop`'s own
   `ModelNotFound` retry loop builds drivers straight from `manifest.fallback_models` and has no
   visibility into `[provider_urls]` at all; before this existed such a fallback was dropped with
   "has API key but no base_url configured" even when `[provider_urls]` had the answer. Called at
   both agent-message dispatch sites (`kernel.rs:2161`, `kernel.rs:2728`), immediately after
   cloning `entry.manifest`.

Net effect for an operator: writing `[[fallback_models]]` for a provider that already has an entry
in `[provider_urls]` — including one added only via `PUT /api/providers/{name}/url` at runtime, not
just `config.toml` — no longer requires also copying that URL into the fallback block by hand. It is
still good practice to set it explicitly when you want the fallback pinned to a specific host
regardless of what `[provider_urls]` says later, since an explicit `fb.base_url` always wins.

---

## 9. Cost tracking

### 9.1 What actually runs

`metering.rs` lives at `crates/openfang-kernel/src/metering.rs` — on this fork and on stock
alike. The fork modified it (FANG-60 gave `MeteringEngine` its own `LlmCounters` state for the
Prometheus `openfang_llm_*` series, §9.4) but did not move it: `git diff --name-status acf2587e...main`
(stock tag vs. the fork's current branch) reports `M`, not `R`. Line numbers below shifted with that change; the path did not.

The live path is `MeteringEngine::estimate_cost_with_catalog()`
(`crates/openfang-kernel/src/metering.rs:283`):

```rust
let (input_per_m, output_per_m) = catalog.pricing(model).unwrap_or((1.0, 3.0));
cost = input_tokens/1e6 * input_per_m + output_tokens/1e6 * output_per_m;
```

**Call sites changed with FANG-60 (§9.1b): the accounting path now runs once per LLM call, not once
per turn.** Current call sites: `kernel.rs:3088` (inside `record_turn_usage`, one call per entry of
`result.calls` — this is the accounting path) and `ws.rs:968` (streaming path, mirrors `kernel.rs:3088`
for the WS turn). A third call site, `kernel.rs:3602` (`session_usage_cost`), is unrelated to
per-call accounting — it estimates a *live* session's running cost from a rough
1-token-≈-4-characters count over `session.messages`, not from `usage_events`, and was not touched
by FANG-60.

`catalog.pricing()` (`model_catalog.rs:307`) is just `find_model(...)` → `(input_cost_per_m,
output_cost_per_m)`.

**The big "Cost Rates" pattern table in `docs/providers.md:773-805` is dead code, unchanged by this
fork.** It lives in `estimate_cost_rates()` (`crates/openfang-kernel/src/metering.rs:322`), which is
called only by `MeteringEngine::estimate_cost()` (`crates/openfang-kernel/src/metering.rs:270`) —
and `estimate_cost` (the non-catalog one) still has **zero non-test callers** in the entire
workspace, verified against the current tree same as against v0.6.9. No
`*haiku*`/`*sonnet*`/`*llama*` heuristic ever runs in production. Unknown model ⇒ flat **$1.00 in /
$3.00 out per million**, full stop.

### 9.1b Per-call accounting, not per-turn (FANG-60)

**Before this fork:** one `UsageRecord` was written per agent turn, priced against the single model
the manifest said the agent used. A turn where the primary model failed mid-turn and a fallback
served the rest booked the *whole turn's* tokens to whichever model happened to be named on the
manifest — the two models' token counts were physically unrecoverable from one row.

**Now:** the unit of accounting is one LLM call. `openfang-types/src/usage.rs` defines `LlmCall` —
`n` (0-based call index = agent-loop iteration), `provider`, `model` (the model that **served** the
call), `requested` (`Some` only on substitution — who was asked for), `reason` (why the requested
model didn't serve it), `input_tokens`, `output_tokens`, `tool_calls`, `cost_usd`. The agent loop
appends one `LlmCall` per LLM round-trip to `AgentLoopResult.calls`; `OpenFangKernel::record_turn_usage`
(`kernel.rs:3058-3098`) then, after the turn finishes:

1. Generates one `turn_id` (UUID) shared by every call of this turn.
2. Prices each call individually via `estimate_cost_with_catalog(&call.model, ...)` — the model
   that **served**, not the one configured — so a turn that fell back mid-way prices each half at
   that half's own rate.
3. Writes one `UsageRecord` (`openfang-memory/src/usage.rs`) per call via
   `MeteringEngine::record_call` → `UsageStore::record`, populating all twelve `usage_events`
   columns as they stand post-v9 (`id, agent_id, timestamp, model, input_tokens, output_tokens,
   cost_usd, tool_calls, provider, turn_id, call_index, requested_model`) for every row this build
   writes — only pre-v9 legacy rows carry `NULL` in the four new columns (see the schema section in
   `architecture.md`).
4. Sums `call.cost_usd` across the turn for the scalar `cost_usd` the API surfaces still expose.

A safety net (`kernel.rs:3066-3079`) synthesizes a single whole-turn `LlmCall` if `result.calls` is
ever empty — the comment calls this "unreachable today" since every agent-loop return path already
populates it, kept only so tokens can't silently vanish if that ever stops being true.

**What `/message`, SSE, WS and `/v1/chat/completions` disclose.** All four read the same
`Vec<LlmCall>`, so they cannot contradict each other about *what happened* — but they do not
share a shape. Read the per-surface table further down before writing a client: an agent that
subscribes to SSE waiting for a `fallback` key will wait forever.

- `model_used` / `provider_used` — the provider and model that served the turn's **last** call
  (`last_served()`, `openfang-types/src/usage.rs`).
- `fallback` — `Option<FallbackSummary>`, present only when some call in the turn was substituted.
  **Six fields**, not four — `used` (always `true` when present), `calls` (how many calls of the
  turn a substitute served), `of` (total calls in the turn), `requested` (what the *first*
  substituted call asked for), `served_by` (de-duplicated list of models that actually served a
  substituted call, in order), `reason` (why the requested model's first substituted attempt
  failed). `fallback_summary()` derives this from `&[LlmCall]` on every read — it is never stored,
  which is why "fell back from X to X" cannot be expressed (`requested` and `served_by` are read
  from *different fields of the same row*).
- `calls` — the full `Vec<LlmCall>`, one entry per call, in order.

**Where each surface puts it — the shapes differ, and this is the part that bites:**

| Surface | Shape |
|---|---|
| `POST /api/agents/{id}/message` | the four fields at the top level of `MessageResponse` (`openfang-api/src/types.rs:59-78`; assembled at `routes.rs:425-429`) |
| SSE `/message/stream` | **no** `model_used` / `fallback` / `calls` anywhere. Instead one `event: call` per LLM call (`routes.rs:1646-1665`): `{n, provider, model, requested, reason, usage:{input_tokens, output_tokens}}` — no `cost_usd`, no turn-level summary. The `done` event keeps its pre-fork shape on purpose, so clients that only parse `done` are untouched |
| WS `/api/agents/{id}/ws` | the `response` message carries `calls[]`, `fallback`, `model_used`, `iterations`, `cost_usd` (`ws.rs:981-997`) |
| `POST /v1/chat/completions` | nested under a vendor key: `obj.insert("openfang", {model_used, provider_used, fallback, calls})` (`openai_compat.rs:365-373`). Read `resp["openfang"]["model_used"]` — `resp["model_used"]` is `null`. The streaming variant carries none of it (`openai_compat.rs:529`, `_ => continue`) |

To reconstruct the turn from SSE, accumulate the `call` events yourself; there is no summary
object on that surface.

**`/api/usage/by-model` (`routes.rs:5755-5757` → `UsageStore::query_by_model`)** groups by `model`
alone, deliberately **not** `(model, provider)` — the in-code comment explains why: grouping by both
would split every model that has rows on both sides of the v9 migration boundary (pre-v9 rows carry
`provider = NULL`) into two dashboard rows keyed the same on the frontend, and the dashboard's
Alpine.js table keeps one DOM node per key, so the second row would silently overwrite the first
one's numbers on screen. Each `ModelUsage` row now also carries `provider` (only when every row for
that model agrees — `NULL` if the model was actually served by more than one provider),
`turn_count` (`COUNT(DISTINCT turn_id)`, distinct from `call_count`), and `substitute_calls` (how
many of the model's calls were substitutions for something else). Per-provider figures without this
model-only-grouping ambiguity live in the `openfang_llm_*` Prometheus series instead (§9.4).

### 9.2 Proven on this box

Both directions measured against the live agent:

| Catalog state | tokens (in/out) | reported `cost_usd` | matches |
|---|---|---|---|
| `openai/gpt-oss-120b` absent | 8306 / 26 | `0.008384` | `8306e-6·1.00 + 26e-6·3.00 = 0.008384` ✔ fallback rates |
| custom entry @ $0.10/$0.50 | 8402 / 47 | `0.0008637` | `8402e-6·0.10 + 47e-6·0.50 = 0.0008637` ✔ catalog rates |

The first row is what a deployment with no catalog entry does: it **over-reports cost by roughly
10×**, and any `max_cost_per_hour_usd` quota is enforced against a fabricated number. The second row
is what a catalog entry with real rates gives you. **This box is in neither state** — see the
blockquote below.

**Fix:** register the custom catalog entry from [§6.3](#63-custom-models--the-catalog-can-be-extended)
with Hyperfusion's real per-million rates. This also gives the agent a correct `context_window`
instead of the 200 000 default.

> **Current live state (re-verified 2026-08-10).** `/data/custom_models.json` exists and registers
> `openai/gpt-oss-120b` on provider `hyperfusion`, `tier: "custom"`, `context_window: 131072`,
> `max_output_tokens: 32768` — which fixes the 200 000-token assumption — but with
> `input_cost_per_m: 0.0` / `output_cost_per_m: 0.0`. So this box no longer over-reports cost 10×;
> it now meters every call at **$0.00**, and every USD budget/quota is therefore inert
> (`GET /api/budget` → `hourly_spend: 0.0`, `daily_spend: 0.0`; `monthly_spend` still carries
> `0.2386317` accumulated before the entry existed). Note the two surfaces differ: the metering
> engine stores a literal `0.0` per call (v9: one `usage_events` row per call, see §9.1b), but the
> `/message` response **drops the `cost_usd` key altogether** — `kernel.rs:3041` sets it to
> `Some(cost)` only when `cost > 0.0` **and** `[usage_footer]` mode is `Cost` or `Full`
> (`UsageFooterMode::Off`/`Tokens` force it to `None` regardless of cost — `kernel.rs:3033-3044`),
> and `openfang-api/src/types.rs` marks the field `skip_serializing_if = "Option::is_none"`. A
> missing `cost_usd` therefore means "priced at zero, or the usage footer is off", not "nothing was
> spent" — check `[usage_footer]` before concluding cost tracking is broken. Put Hyperfusion's real
> per-million rates in that file if you want meaningful cost numbers or a working
> `max_cost_per_hour_usd`. Re-read the file rather than trusting this paragraph — another session
> mutates this instance.

### 9.3 Quotas

`max_cost_per_hour_usd` under an agent's `[resources]`; checked on every LLM call, rejecting with
`QuotaExceeded`. Global defaults come from `[budget]` via `apply_budget_defaults`
(`kernel.rs:1692`).

### 9.4 `GET /api/metrics` — five series, three questions (FANG-60)

Verified live against this box (`curl -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/metrics`).
Do not read the first matching line and stop — two of these series look like they answer "how much
did model X cost" and only one of them actually does.

| Series | Type | Labels | What it answers | Source |
|---|---|---|---|---|
| `openfang_tokens_total` | gauge | `agent`, `provider`, `model` | Per-**agent** rolling-hourly token total across *all* models that agent used. **Frozen, on purpose**: `provider`/`model` describe the agent's *configuration*, not what actually served any given token — changing that would move an agent's tokens between series turn by turn and break `sum()`/`increase()` in Prometheus. | `routes.rs:3680-3681`; HELP text says so explicitly ("for per-model truth use openfang_llm_tokens_total") |
| `openfang_tool_calls_total` | gauge | `agent` | Tool calls requested by the model, rolling hourly window, **counted per LLM response** (a response asking for 3 tool calls in parallel counts 3). Was **identically 0 before this fork** for lack of instrumentation — briefly counted "iterations that used a tool" instead, which undercounts whenever one response asks for more than one call. | `routes.rs:3682-3683` (HELP text states the history verbatim) |
| `openfang_llm_calls_total` | counter | `agent`, `provider`, `model` | LLM calls **by the provider/model that actually served them** — the per-call truth `openfang_tokens_total`'s labels can't give you, monotonic since process start (resets only on daemon restart, which Prometheus already handles). | `routes.rs:3606-3611` |
| `openfang_llm_tokens_total` | counter | `agent`, `provider`, `model`, `direction` (`input`/`output`) | Tokens by the provider/model that actually served them. | `routes.rs:3613-3621` |
| `openfang_llm_fallback_calls_total` | counter | `agent`, `requested`, `served` | Calls served by a substitute model — only emitted for turns with an actual substitution, so an agent with a healthy primary emits no series for this metric at all (absence ≠ zero in the usual Prometheus sense; it means "never queried", not "queried and found zero"). | `routes.rs:3624-3630` |

**Which one answers "which model burned the tokens this hour"?** `openfang_llm_tokens_total`, never
`openfang_tokens_total` — the latter's labels are frozen to the agent's config and will lie about the
model on any turn a fallback served. Verified live on this box, on one agent (`<agent-name>`): its
`openfang_tokens_total{...,model="google/gemma-4-31b-it"}` reads `88267`, and its
`openfang_llm_calls_total{...,model="google/gemma-4-31b-it"}` reads `5` calls for `88267` combined
input+output tokens — consistent here only because that agent has never fallen back; the two series
are not guaranteed to reconcile for an agent that has.

`render_agent_usage_metrics()` (`routes.rs:3585`) and `render_llm_call_metrics()` (`routes.rs:3601`)
are split out from the main handler (`prometheus_metrics`, `routes.rs:3647`) specifically so a test
can pin the exact label set — the doc-comment on `render_agent_usage_metrics` names the regression
class this guards against: a label set changing between two scrapes of the *same* series makes
Prometheus treat it as a new series and starts the old one's counter over from the scraper's point of
view, silently truncating history.

## 10. Model routing

`ModelRouter` — `crates/openfang-runtime/src/routing.rs`.

**Configured on the agent manifest as `routing`, not in `config.toml`.** `AgentManifest.routing:
Option<ModelRoutingConfig>` (`agent.rs:470`). `KernelConfig` has **no** `routing` field — the
`docs/providers.md:757` snippet implying a top-level `[routing]` in `config.toml` is wrong.

```toml
[routing]                                # inside the AGENT manifest toml
simple_model     = "claude-haiku-4-5-20251001"
medium_model     = "gemini-2.5-flash"
complex_model    = "claude-sonnet-4-20250514"
simple_threshold = 100
complex_threshold = 500
```

Defaults at `agent.rs:57-66`. Scoring (`routing.rs:51`):

| Signal | Contribution |
|---|---|
| total message chars / 4 | +1 per pseudo-token |
| tools defined | +20 × tool count |
| code markers in **last** message | +30 × distinct markers matched |
| messages beyond 10 | +15 each |
| system prompt chars beyond 500 | +1 per 10 chars |

Markers: `` ``` ``, `fn `, `def `, `class `, `import `, `function `, `async `, `await `, `struct `,
`impl `, `return ` (lowercased `contains`). Classification: `< simple_threshold` → Simple;
`>= complex_threshold` → Complex; else Medium.

Applied at `kernel.rs:2889-2919`, and **only when the kernel is not in `Stable` mode** — Stable takes
the `pinned_model` branch instead (`kernel.rs:2878-2887`). Aliases are resolved before scoring
(`routing.rs:153`). After selection the routed model can also **change the agent's provider**
(`kernel.rs:2911-2918`) — and the routed id is *not* re-stripped afterwards, so a Class-A prefixed
routing target is normalised later in `resolve_driver`.

`validate_models()` (`routing.rs:136`) returns warning strings for ids missing from the catalog; it
does not block.

---

## 11. API endpoints

Routes registered in `crates/openfang-api/src/server.rs:594-628`. All require
`Authorization: Bearer <API_KEY>` when `api_key` is set in `config.toml`.

| Method | Path | Handler | Notes |
|---|---|---|---|
| GET | `/api/models` | `routes.rs:6322` | `{"models":[…],"available":N,"total":N}` — **wrapped object, not a bare array** |
| GET | `/api/models/aliases` | `routes.rs:6399` | 89 entries live |
| GET | `/api/models/{*id}` | `routes.rs:6427` | wildcard, so slashes work raw |
| POST | `/api/models/custom` | `routes.rs:6561` | 201; persists `custom_models.json` |
| DELETE | `/api/models/custom/{*id}` | `routes.rs:6659` | `Custom` tier only |
| GET | `/api/providers` | `routes.rs:6476` | `{"providers":[…],"total":42}` |
| POST | `/api/providers/{name}/key` | `routes.rs:7665` | body **`{"key": "…"}`** |
| DELETE | `/api/providers/{name}/key` | `routes.rs:7842` | |
| POST | `/api/providers/{name}/test` | `routes.rs:7898` | 404 for non-catalog providers |
| PUT | `/api/providers/{name}/url` | `routes.rs:7999` | body `{"base_url": "https://…"}` |
| POST | `/api/providers/github-copilot/oauth/start` | `routes.rs` | device flow |
| GET | `/api/providers/github-copilot/oauth/poll/{poll_id}` | | |

### 11.1 `POST /api/providers/{name}/key` — what it writes

`docs/providers.md:906` documents the body as `{"api_key": "sk-…"}`. **That is wrong** — the handler
reads `body["key"]` (`routes.rs:7670`). Verified live: posting `{"api_key":"…"}` returns
`400 {"error":"Missing or empty 'key' field"}` and writes nothing.

Correct call:

```bash
curl -X POST -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  -d '{"key":"<SECRET>"}' http://127.0.0.1:4200/api/providers/venice/key
```

On success it performs **four** writes plus a possible fifth:

1. **Vault** — `state.kernel.store_credential(env_var, key)` (best-effort no-op if uninitialised).
2. **`{home_dir}/secrets.env`** — dual-write via `write_secret_env` (`routes.rs:7701`). Here:
   `/data/secrets.env`. A failure here is the only fatal path (500).
3. **Process env** — `std::env::set_var(env_var, key)` so `detect_auth()` sees it immediately.
4. **Catalog** — `detect_auth()` re-run.
5. **`{home_dir}/config.toml`** — *conditionally*. If the **current default provider has no working
   key** and differs from `{name}`, the handler rewrites `[default_model]` to point at `{name}` with
   `catalog.default_model_for_provider(name)`, takes a `.bak` first (`backup_config`), and hot-swaps
   `kernel.default_model_override`. Response then carries `"switched_default": true`.

The env var name is `catalog.get_provider(name).api_key_env`, or for unknown providers the derived
`{NAME_UPPER}_API_KEY` (`routes.rs:7688-7693`).

> On this box the auto-switch is **inert** because the current default (`hyperfusion`) has a working
> `HYPERFUSION_API_KEY`, so `current_has_key == true`. If that key were ever unset, the next
> `set_provider_key` call for any other provider would silently rewrite `[default_model]`.

### 11.2 `POST /api/providers/{name}/test` caveats

- Returns **HTTP 200 even on failure**; the real result is in the `status` field
  (`"ok"` vs `"error"`). Do not script on the status code.
- `404` if the provider is not in the catalog — so custom providers cannot be tested until
  registered via `[provider_urls]` / `PUT …/url`. Verified: `hyperfusion` → `404 Unknown provider`.
- **It does not strip provider prefixes.** It sends `catalog.default_model_for_provider(name)`
  verbatim (`routes.rs:7957`). For `openrouter` that is `openrouter/google/gemini-2.5-flash`, which
  OpenRouter will reject — so the test reports failure even with a perfectly good key. Same for
  `requesty`, `cerebras`, `chutes`, `bedrock`, `codex`, `azure`, `claude-code`, `qwen-code`.

### 11.3 CLI equivalents

```bash
docker exec openfang-openfang-1 openfang models list       # optional provider filter
docker exec openfang-openfang-1 openfang models aliases    # BROKEN — see below
docker exec openfang-openfang-1 openfang models providers  # JSON
docker exec openfang-openfang-1 openfang models set openai/gpt-oss-120b   # daemon default model
docker exec openfang-openfang-1 openfang agent list        # shows PROVIDER and MODEL columns
docker exec openfang-openfang-1 openfang agent set <AGENT_UUID> model <VALUE>
```

`openfang models aliases` is **broken**: it exits 0 and prints only

```
ALIAS                          RESOLVES TO
------------------------------------------------------------
aliases                        ?
total                          ?
```

because it walks the keys of the `{"aliases":[…],"total":N}` wrapper object instead of the array.
Use `curl -s http://127.0.0.1:4200/api/models/aliases` (or `ofctl GET /api/models/aliases`) for the
real 89 entries.

A bare `openfang models set` opens an **interactive picker** (`[MODEL]  Model ID or alias …
Interactive picker if omitted`). Under `docker exec` with no `-it` there is no TTY, so it dumps the
whole menu to stdout and then cannot be driven — pass the model as an argument, as above, or use
`docker exec -it`.

`openfang agent set` takes a **UUID**, not a name, and has no `--provider` flag.
There is no `openfang agent show`.

---

## 12. Auth detection

`detect_auth()` — `model_catalog.rs:56`. Presence-only `std::env::var()` checks; never reads or logs
the value. Special cases evaluated **before** the `key_required` check:

| Provider | Probe |
|---|---|
| `claude-code` | `claude_code_available()` — CLI on PATH |
| `qwen-code` | `qwen_code_available()` |
| `github-copilot` / `copilot` | persisted OAuth tokens under `$HOME/.openfang` |
| `gemini` | `GEMINI_API_KEY` **or** `GOOGLE_API_KEY` |
| `codex` | `OPENAI_API_KEY` **or** a Codex CLI credential file |
| anything with `key_required = false` | `AuthStatus::NotRequired` |

`AuthStatus` (`openfang-types/src/model_catalog.rs:107`): `Configured | Missing | NotRequired`,
serialised lowercase (`"missing"`, `"not_required"`).

`available_models()` (`model_catalog.rs:293`) = models whose provider is **not** `Missing`. On this
box that is 8 models (6 Ollama + vLLM + LM Studio) — none of which we actually use, because the
`hyperfusion` provider isn't in the catalog at all. **`available: false` in `/api/models` says
nothing about whether a model works.**

> **The two `available`s in `/api/models` disagree.** The envelope's `"available": 8` is
> `available_models().len()` (provider `auth_status != Missing`), while each model's own
> `"available"` flag is `…unwrap_or(m.tier == ModelTier::Custom)` (`routes.rs:6364-6367`). Counting
> the per-model flags live gives **9** — the extra one is `openai/gpt-oss-120b`, true purely because
> its tier is `Custom`. Verified live: `total 206, available 8, models-with-available=true 9`.

`detect_available_provider()` (`drivers/mod.rs:603`) is the boot-time auto-detect fallback used when
the configured primary driver fails to construct (`kernel.rs:712-742`); it scans a hard-coded
priority list of env vars and **overwrites `config.default_model`** in memory if it finds one.

---

## 13. Where `docs/providers.md` lies

| Doc claim | Reality | Evidence |
|---|---|---|
| "3 native LLM drivers" | 8 drivers + 1 wrapper | `drivers/mod.rs:7-15` |
| "20 providers" | 42 | `model_catalog.rs:572`; live `total: 42` |
| "51 builtin models" (table lists 53) | 205 builtin (live `total: 206` here — 205 + 1 custom) | `model_catalog.rs:1067` |
| "23 aliases" | 89 live (68 builtin pairs + 74 entry aliases) | `model_catalog.rs:967` |
| `sonnet → claude-sonnet-4-20250514` | `claude-sonnet-4-6` | `model_catalog.rs:969` |
| `POST …/key` body `{"api_key": …}` | `{"key": …}` | `routes.rs:7670`; live 400 |
| `GET /api/models` returns a bare array | returns `{"models":…,"available":…,"total":…}` | live |
| `GET /api/providers` returns a bare array | returns `{"providers":…,"total":…}` | live |
| Fallback does **not** failover on 429/529 | it does failover on every error | `fallback.rs:46-66` |
| `fallback_models = ["a","b"]` | array of `{provider, model}` tables | `agent.rs:407,443` |
| `[routing]` in `config.toml` | agent-manifest field only | `agent.rs:470`; no `routing` in `KernelConfig` |
| Cost-rate pattern table | dead code; catalog-only + $1/$3 fallback | `crates/openfang-kernel/src/metering.rs:283` (line moved this fork; the file did not) |
| Module comment: "130+ models across 28 providers" | 205 / 42 | `model_catalog.rs:3` |
| `[provider_urls]`, `[provider_api_keys]` | exist and are important | undocumented anywhere in `docs/` |

---

## 14. Issue status against v0.6.9

| Issue | Title | State | Verdict against the code |
|---|---|---|---|
| **#1195** | OpenAI-compatible custom base_url strips `openai/` from Featherless model IDs | OPEN | **Real, unfixed, root cause = `agent_loop.rs:212`.** Reproduces whenever the model id starts with `"{provider}/"`. Reporter's workaround is explained exactly by the code. **Does not affect us** (provider `hyperfusion`) — proven by a discriminating upstream probe. |
| **#1149** | Migrate OpenAI support to Responses API | OPEN | Accurate. `OpenAIDriver` is Chat Completions only — `chat_url()` (`openai.rs:79`) hard-codes `/chat/completions`; no `/responses` anywhere. Nothing started. |
| **#995** | Add Requesty as a Provider | OPEN | **Stale — already implemented.** `requesty` exists in the catalog (`model_catalog.rs:629`, 5 models, `https://router.requesty.ai/v1`) and in `provider_defaults` (`drivers/mod.rs:109`). `crates/openfang-kernel/src/metering.rs:323` even has a Requesty pricing block citing #995. Should be closed. Caveat: `POST /api/providers/requesty/test` will still fail because the default model id `requesty/anthropic/claude-sonnet-4` is sent unstripped ([§11.2](#112-post-apiprovidersnametest-caveats)). |
| **#981** | MiniMax Coding Plan fails with Auth 401 | OPEN | **Real.** `minimax` is wired to `OpenAIDriver` via `provider_defaults` (`drivers/mod.rs:234`) at `https://api.minimax.io/v1`. The reported error body (`{"type":"error","error":{"type":"authorized_error",…}}`) is **Anthropic-shaped**, i.e. the Coding Plan endpoint speaks the Anthropic protocol. OpenFang has `zhipu_coding`, `zai_coding`, `kimi_coding` and `volcengine_coding` variants — and `kimi_coding` is explicitly routed to `AnthropicDriver` (`drivers/mod.rs:509-522`) — but there is **no `minimax_coding` provider**. That missing sibling is the gap. |
| **#1033** | Support OpenAI Codex App Server as a model backend | OPEN | Accurate. The existing `codex` provider is *not* this: it is `OpenAIDriver` against `https://api.openai.com/v1`, keyed by `OPENAI_API_KEY` or a credential scraped from the Codex CLI (`read_codex_credential`, `model_catalog.rs:525`). No app-server subprocess, no stdio handshake, no ChatGPT login flow. |

Related closed issues whose fixes you can see in the code: **#856** (custom models shadowed by
builtins → `find_model` user-defined priority, `model_catalog.rs:131`), **#833** (provider-scoped
lookup, `model_catalog.rs:199`), **#845** (per-agent `fallback_models`, `agent_loop.rs:1253`),
**#1032** (OpenRouter free aliases pointing at tool-capable models, `model_catalog.rs:1050-1066`).
**#1154** (local provider host env vars) is still OPEN but partially addressed by
`local_provider_url_from_env`.

---

## 15. Gotchas (all verified)

1. **`grep '^api_key' config.toml` returns TWO lines.** It also matches `api_key_env`. The recipe
   `K=$(grep '^api_key' … | cut -d'"' -f2)` yields a 71-char string containing a newline
   (`<key>\nHYPERFUSION_API_KEY`), and every authenticated request then fails with an opaque
   **HTTP 400, zero-length body** (full explanation, including why `-m1` alone is not enough:
   `automation-workflows-triggers-schedules.md` gotcha 46). Use:
   ```bash
   K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)
   ```
   Correct key length here is 51.

2. **`strip_provider_prefix` is a blind string match.** Any model whose id begins with
   `"{provider}/"` or `"{provider}:"` is silently truncated — no log, no error. This is issue #1195.
   Pick provider names that don't collide with model namespaces.

3. **The mangled id gets persisted, not just sent.** `kernel.rs:1686` (registration) and
   `kernel.rs:3367` (model switch) write the stripped value into the agent registry, so the damage
   survives restarts and isn't visible by re-reading `config.toml`.

4. **Our shield is the provider *name*, not `base_url`.** `strip_provider_prefix` fires on every
   request (`agent_loop.rs:528`, `:1765`) irrespective of `base_url`; `"openai/gpt-oss-120b"` simply
   does not start with `"hyperfusion/"`. `kernel.rs:3347-3356`'s `has_custom_url` guard only
   suppresses provider auto-detection in the model-switch path. Dropping `base_url` on its own is
   harmless; renaming the provider to `openai`, or switching to an uncatalogued model so
   `infer_provider_from_model` (`kernel.rs:6876-6901`) rewrites the provider to `openai`, is what
   breaks it.

5. **A custom provider defined only in `[default_model]` is invisible to `/api/providers`.**
   `hyperfusion` is absent from the 42-entry list, so the dashboard, `/models` availability flags,
   and `POST …/test` all behave as if it doesn't exist. Register it in `[provider_urls]` to fix.

6. **Unknown model ⇒ $1.00/$3.00 per M invented pricing** (`crates/openfang-kernel/src/metering.rs:289`), which is ~10× off for
   `gpt-oss-120b`. Quotas and the usage footer are enforced/printed against that fiction. The
   documented pattern-matching rate table never executes — `estimate_cost` has no non-test callers.

7. **Unknown model ⇒ 200 000-token assumed context window** (`agent_loop.rs:225,502`), regardless of
   the model's real limit. Add a custom catalog entry with the true `context_window` or risk
   overflow behaviour tuned for the wrong number.

8. **`POST /api/providers/{name}/key` takes `{"key": …}`, not `{"api_key": …}`.** The documented body
   silently 400s.

9. **That same endpoint can rewrite `[default_model]` in `config.toml`** when the current default
   provider has no working key — a "just save this key" action can repoint the whole daemon. It does
   take a `.bak` first.

10. **`PUT /api/providers/{name}/url` rewrites `config.toml` through a TOML serialiser**
    (`routes.rs:8103`), destroying comments and ordering, and takes **no backup**.

11. **`POST /api/providers/{name}/test` returns HTTP 200 on failure** — check the `status` field. It
    also 404s for custom providers and gives false negatives for every provider whose catalog
    default id carries a Class-A prefix (`openrouter`, `requesty`, `cerebras`, `chutes`, `bedrock`,
    `codex`, `azure`, `claude-code`, `qwen-code`).

12. **`available` in `/api/models` is meaningless in BOTH directions for custom providers.**
    `routes.rs:6364-6367` computes it as `.unwrap_or(m.tier == ModelTier::Custom)` — so a model on a
    provider the catalog has never heard of reports `available: true` purely because its tier is
    `Custom`. Verified live: `openai/gpt-oss-120b` showed `available: true` while every real cloud
    model showed `false`. `false` only means the provider's `auth_status` is `Missing`. Worse, the
    envelope's `"available"` **count** uses a different rule (`available_models()`), so it reads
    `8` while nine models carry `available: true`. Trust neither number.

12b. **`POST /api/providers/{name}/test` false-negatives for 15 of the 42 built-ins.** Twelve have a
    first-catalog-model id beginning with `"{provider}/"`, which is sent unstripped: `azure`,
    `bedrock`, `cerebras`, `chutes`, `claude-code`, `codex`, `nvidia`, `openrouter`, `qwen-code`,
    `replicate`, `requesty`, `sambanova`. Three more (`github-copilot`, `lemonade`, `zai`) have zero
    catalog models, so `default_model_for_provider` returns `""` and the test posts an empty model
    id. A perfectly good key still reports failure.

13. **Per-agent `fallback_models` are NOT prefix-stripped** (`agent_loop.rs:1285`) while global
    `[[fallback_providers]]` **are** (`kernel.rs:783`). Same concept, opposite handling.

14. **Per-agent `fallback_models` only fire on `ModelNotFound`** — they are not a general failover.
    Use `[[fallback_providers]]` for 5xx/429 resilience.

15. **Fallback failover happens on rate limits too**, contradicting the docs — a 429 on the primary
    will silently move traffic (and spend) to your fallback provider.

16. **An empty API key produces an unauthenticated request, not an error** (`openai.rs:107`,
    `drivers/mod.rs:557`). You get the upstream's 401 rather than a clear OpenFang diagnostic.

17. **`find_model` matches on `display_name` too** (`model_catalog.rs:176-181`). A model id that happens
    to equal some other model's display name resolves to the wrong entry.

18. **`add_custom_model` defaults `provider` to `"openrouter"`** (`routes.rs:6573`) and both costs to
    `0.0`. Omit `provider` and your entry lands on the wrong provider with free pricing.

19. **`novita`/`novita-ai` works but has no catalog entry** (`provider_defaults` arm at
    `drivers/mod.rs:289`, zero catalog models → absent from the 42-entry `/api/providers`).
    Conversely only `anthropic`, `bedrock` and `qwen-code` have catalog entries with no
    `provider_defaults` arm, and all three have dedicated driver branches (`drivers/mod.rs:337`,
    `:497`, `:416`) so the absence is deliberate. `claude-code` (`:214`) and `github-copilot`
    (`:204`) **do** have arms. The three registries are not synchronised.

20. **`claude-code` and `qwen-code` reuse `base_url` as the CLI executable path**
    (`drivers/mod.rs:390`, `:417`) — setting it to a URL will fail confusingly.

21. **Azure requires `base_url`** and errors out without it (`drivers/mod.rs:443`); its
    `provider_defaults` entry is unreachable dead code because the azure branch returns first.

22. **Kimi K2 silently changes host.** `chat_url` redirects `api.moonshot.ai` → the `.cn` endpoint
    when the model starts with `kimi-k2` (`openai.rs:88-95`).

23. **`docs/providers.md` is stale across the board** — see [§13](#13-where-docsprovidersmd-lies).
    Treat it as historical.

---

## 16. Verification recipes

```bash
D=/var/lib/docker/volumes/openfang_openfang-data/_data
K=$(grep -m1 '^api_key = ' $D/config.toml | cut -d'"' -f2)   # 51 chars

# what the kernel thinks the agent runs
docker exec openfang-openfang-1 openfang agent list

# catalog / provider snapshots
curl -s -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/providers \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["total"])'
curl -s -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/models \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["total"],d["available"])'

# THE prefix-strip test: does the upstream distinguish the two ids?
set -a; . $D/secrets.env; set +a
for M in "openai/gpt-oss-120b" "gpt-oss-120b"; do
  printf '%s -> ' "$M"
  curl -s -o /dev/null -w '%{http_code}\n' \
    -H "Authorization: Bearer $HYPERFUSION_API_KEY" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$M\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":5}" \
    https://api.hyperfusion.io/v1/chat/completions
done
# expected: openai/gpt-oss-120b -> 200 ; gpt-oss-120b -> 401
# then confirm the agent path also returns 200:
curl -s -X POST -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  -d '{"message":"Reply with exactly: PONG"}' \
  http://127.0.0.1:4200/api/agents/17ffd1ca-b548-4132-a305-df3e32fbd9e3/message
```

Cost check: compare the returned `cost_usd` against
`in/1e6*1.00 + out/1e6*3.00`. If it matches, the model is **not** in the catalog and pricing is
fabricated. If the key is missing entirely, the catalogued price is 0.0 — the field is only
serialised when cost > 0 (§9.2), so absence is not evidence that the catalog entry is correct.

---

## 17. Recommended changes for this box

1. **Fill in real Hyperfusion rates on the existing custom catalog entry.** The entry for
   `openai/gpt-oss-120b` under provider `hyperfusion` is already there with the right
   `context_window = 131072` — context budgeting is fixed — but its costs are `0.0/0.0`, so every
   cost report and budget check reads $0. Re-adding is a `409`; `DELETE` then re-`POST`.
   ([§6.3](#63-custom-models--the-catalog-can-be-extended), live state in
   [§9.2](#92-proven-on-this-box))
2. **Register the provider** in `[provider_urls]` so it appears in `/api/providers` and the dashboard
   stops showing 42 "missing" providers and nothing that works. **Still outstanding** — the live
   `config.toml` has no `[provider_urls]` section (`config.toml.bak` still does), so `hyperfusion` is
   absent from the 42-entry `/api/providers`; `ofdoctor` reports this as a WARN. Inference works
   regardless, from `[default_model] base_url`.
   ([§6.2](#62-making-the-custom-provider-visible-in-apiproviders))
3. **Leave the provider named `hyperfusion`.** Add a comment in `config.toml` saying why, so nobody
   "tidies" it to `openai` and hits #1195.
4. **Do not rely on `base_url` as the #1195 shield** — the provider *name* is the shield. Keeping
   `base_url` on the manifest is still worth doing (it suppresses provider auto-detection inside
   `POST /api/agents/{id}/model`, `kernel.rs:3347-3356`), but it does not stop
   `strip_provider_prefix`, which runs on every request regardless.
5. If you add `[[fallback_providers]]`, remember it will also absorb 429s, and budget for that.
