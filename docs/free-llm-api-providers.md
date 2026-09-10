# 免费 LLM API 提供商汇总

> 整理时间：2025-09 | 所有接口均为 OpenAI SDK 兼容（可直接用 `openai` Python 库调用）
>
> ⚠️ 免费额度可能随时调整，请以各平台官方页面为准。

---

## 目录

- [快速推荐](#快速推荐)
- [1. Google Gemini](#1-google-gemini)
- [2. Groq](#2-groq)
- [3. OpenRouter](#3-openrouter)
- [4. Mistral AI](#4-mistral-ai)
- [5. NVIDIA NIM](#5-nvidia-nim)
- [6. Cloudflare Workers AI](#6-cloudflare-workers-ai)
- [7. Cohere](#7-cohere)
- [8. SiliconFlow](#8-siliconflow)
- [9. Z AI (智谱)](#9-z-ai-智谱)
- [10. OVHcloud (匿名免费)](#10-ovhcloud-匿名免费)
- [11. Kilo Code](#11-kilo-code)
- [12. Hugging Face](#12-hugging-face)
- [附录：通用调用代码](#附录通用调用代码)

---

## 快速推荐

| 场景 | 推荐 | 理由 |
|------|------|------|
| 🏆 综合最佳 | **Google Gemini** | 1M 上下文、免费额度最大、中文强 |
| ⚡ 最快速度 | **Groq** | LPU 芯片、亚秒级响应 |
| 🇨🇳 国内用户 | **SiliconFlow / 智谱** | 无需翻墙、中文优化 |
| 🔑 无需注册 | **OVHcloud** | 匿名调用、零配置 |
| 🎯 多模型选择 | **OpenRouter** | 17+ 免费模型可选 |

---

## 1. Google Gemini

| 项目 | 详情 |
|------|------|
| 🔗 注册 | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| 💳 需要信用卡 | ❌ 不需要 |
| 📍 Base URL | `https://generativelanguage.googleapis.com/v1beta/openai` |
| 🔑 环境变量 | `GOOGLE_API_KEY` |

### 免费模型

| 模型 | 上下文 | 最大输出 | 速率限制 |
|------|--------|---------|---------|
| `gemini-2.0-flash` | 1M | 65K | 15 RPM, 1500 RPD |
| `gemini-2.5-flash` | 1M | 65K | 15 RPM, 1500 RPD |
| `gemini-2.5-flash-lite` | 1M | 65K | 30 RPM, 1500 RPD |
| `gemini-1.5-flash` | 1M | 65K | 15 RPM, 1500 RPD |
| `gemini-2.5-pro` | 1M | 65K | 5 RPM, 50 RPD |
| `gemma-4-31b` | 256K | 32K | — |
| `gemma-4-26b-a4b` | 256K | 32K | — |

### 调用示例

```python
import openai

client = openai.OpenAI(
    api_key="YOUR_GOOGLE_API_KEY",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
)

response = client.chat.completions.create(
    model="gemini-2.0-flash",
    messages=[
        {"role": "system", "content": "你是小忆，桌面猫咪AI助手喵~"},
        {"role": "user", "content": "你好！"},
    ],
    max_tokens=512,
    temperature=0.7,
)
print(response.choices[0].message.content)
```

---

## 2. Groq

| 项目 | 详情 |
|------|------|
| 🔗 注册 | [console.groq.com/keys](https://console.groq.com/keys) |
| 💳 需要信用卡 | ❌ 不需要 |
| 📍 Base URL | `https://api.groq.com/openai/v1` |
| 🔑 环境变量 | `GROQ_API_KEY` |
| ⚡ 特点 | LPU 芯片推理，速度极快（450+ tokens/s） |

### 免费模型

| 模型 | 上下文 | 最大输出 | 速率限制 |
|------|--------|---------|---------|
| `llama-3.1-8b-instant` | 128K | 8K | 30 RPM, 1000 RPD |
| `gemma2-9b-it` | 8K | 8K | 30 RPM, 1000 RPD |
| `mixtral-8x7b-32768` | 32K | 32K | 30 RPM, 1000 RPD |
| `llama-3.3-70b-versatile` | 128K | 32K | 30 RPM, 1000 RPD |

### 调用示例

```python
import openai

client = openai.OpenAI(
    api_key="YOUR_GROQ_API_KEY",
    base_url="https://api.groq.com/openai/v1",
)

response = client.chat.completions.create(
    model="llama-3.1-8b-instant",
    messages=[
        {"role": "system", "content": "你是小忆，桌面猫咪AI助手喵~"},
        {"role": "user", "content": "你好！"},
    ],
    max_tokens=512,
    temperature=0.7,
)
print(response.choices[0].message.content)
```

---

## 3. OpenRouter

| 项目 | 详情 |
|------|------|
| 🔗 注册 | [openrouter.ai/keys](https://openrouter.ai/keys) |
| 💳 需要信用卡 | ❌ 不需要（充值 $10 可提升免费模型限额） |
| 📍 Base URL | `https://openrouter.ai/api/v1` |
| 🔑 环境变量 | `OPENROUTER_API_KEY` |
| 🎯 特点 | 17+ 免费模型、自动路由、模型降级 |

### 免费模型（带 `:free` 后缀）

| 模型 | 上下文 | 最大输出 | 速率限制 |
|------|--------|---------|---------|
| `nvidia/nemotron-3-super-120b-a12b:free` | 262K | 262K | 20 RPM, 50 RPD |
| `openai/gpt-oss-20b:free` | 131K | 32K | 20 RPM, 50 RPD |
| `google/gemma-4-26b-a4b-it:free` | 262K | 32K | 20 RPM, 50 RPD |
| `google/gemma-4-31b-it:free` | 262K | 32K | 20 RPM, 50 RPD |
| `inclusionai/ling-3.0-flash:free` | 262K | 32K | 20 RPM, 50 RPD |
| `cohere/north-mini-code:free` | 256K | 64K | 20 RPM, 50 RPD |
| `nvidia/nemotron-3-nano-30b-a3b:free` | 256K | — | 20 RPM, 50 RPD |
| `nvidia/nemotron-nano-9b-v2:free` | 128K | — | 20 RPM, 50 RPD |
| `nvidia/nemotron-nano-12b-v2-vl:free` | 128K | 128K | 20 RPM, 50 RPD |
| `poolside/laguna-s-2.1:free` | 262K | 32K | 20 RPM, 50 RPD |
| `poolside/laguna-xs-2.1:free` | 262K | 32K | 20 RPM, 50 RPD |

> 💡 充值 $10+ 可将免费模型限额提升至 1000 RPD

### 调用示例

```python
import openai

client = openai.OpenAI(
    api_key="YOUR_OPENROUTER_API_KEY",
    base_url="https://openrouter.ai/api/v1",
)

response = client.chat.completions.create(
    model="nvidia/nemotron-3-super-120b-a12b:free",
    messages=[
        {"role": "system", "content": "你是小忆，桌面猫咪AI助手喵~"},
        {"role": "user", "content": "你好！"},
    ],
    max_tokens=512,
    temperature=0.7,
)
print(response.choices[0].message.content)
```

---

## 4. Mistral AI

| 项目 | 详情 |
|------|------|
| 🔗 注册 | [console.mistral.ai/api-keys](https://console.mistral.ai/api-keys) |
| 💳 需要信用卡 | ❌ 不需要 |
| 📍 Base URL | `https://api.mistral.ai/v1` |
| 🔑 环境变量 | `MISTRAL_API_KEY` |
| 🆓 额度 | $10/月 API 抵扣额度 |

### 免费模型

| 模型 | 上下文 | 模态 |
|------|--------|------|
| `mistral-medium-latest` (128B) | 256K | Text + Image + Code |
| `mistral-small-latest` | 256K | Text + Image + Code |
| `mistral-large-latest` | 256K | 多模态 |
| `ministral-3b-latest` | 256K | Text + Vision |
| `ministral-8b-latest` | 256K | Text + Vision |
| `codestral-latest` | 128K | Code |

### 调用示例

```python
import openai

client = openai.OpenAI(
    api_key="YOUR_MISTRAL_API_KEY",
    base_url="https://api.mistral.ai/v1",
)

response = client.chat.completions.create(
    model="mistral-small-latest",
    messages=[
        {"role": "system", "content": "你是小忆，桌面猫咪AI助手喵~"},
        {"role": "user", "content": "你好！"},
    ],
    max_tokens=512,
    temperature=0.7,
)
print(response.choices[0].message.content)
```

---

## 5. NVIDIA NIM

| 项目 | 详情 |
|------|------|
| 🔗 注册 | [build.nvidia.com](https://build.nvidia.com/explore/discover) |
| 💳 需要信用卡 | ❌ 不需要（NVIDIA 开发者账号） |
| 📍 Base URL | `https://integrate.api.nvidia.com/v1` |
| 🔑 环境变量 | `NVIDIA_API_KEY` |
| 🎯 特点 | 100+ 模型、40 RPM、10000 RPD |

### 免费模型（精选）

| 模型 | 上下文 | 最大输出 |
|------|--------|---------|
| `nvidia/nemotron-3-super-120b-a12b` | 1M | 262K |
| `nvidia/nemotron-3-nano-30b-a3b` | 262K | 32K |
| `nvidia/llama-3.1-nemotron-ultra-253b-v1` | 128K | 4K |
| `meta/llama-3.3-70b-instruct` | 128K | 4K |
| `google/gemma-4-31b-it` | 262K | 8K |
| `openai/gpt-oss-120b` | 131K | 131K |
| `openai/gpt-oss-20b` | 131K | 131K |
| `minimaxai/minimax-m3` | 1M | ~64K |

### 调用示例

```python
import openai

client = openai.OpenAI(
    api_key="YOUR_NVIDIA_API_KEY",
    base_url="https://integrate.api.nvidia.com/v1",
)

response = client.chat.completions.create(
    model="meta/llama-3.3-70b-instruct",
    messages=[
        {"role": "system", "content": "你是小忆，桌面猫咪AI助手喵~"},
        {"role": "user", "content": "你好！"},
    ],
    max_tokens=512,
    temperature=0.7,
)
print(response.choices[0].message.content)
```

---

## 6. Cloudflare Workers AI

| 项目 | 详情 |
|------|------|
| 🔗 注册 | [dash.cloudflare.com](https://dash.cloudflare.com/profile/api-tokens) |
| 💳 需要信用卡 | ❌ 不需要 |
| 📍 Base URL | `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run` |
| 🔑 环境变量 | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` |
| 🆓 额度 | 10,000 Neurons/天（所有模型共享） |

### 免费模型（精选）

| 模型 | 上下文 |
|------|--------|
| `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | 24K |
| `@cf/meta/llama-4-scout-17b-16e-instruct` | 131K |
| `@cf/openai/gpt-oss-120b` | 128K |
| `@cf/google/gemma-4-26b-a4b-it` | 256K |
| `@cf/zai-org/glm-4.7-flash` | 131K |
| `@cf/deepseek-ai/deepseek-r1-distill-qwen-32b` | 80K |
| `@cf/mistralai/mistral-small-3.1-24b-instruct` | 128K |

> ⚠️ Cloudflare API 格式与 OpenAI 略有不同，需要适配

---

## 7. Cohere

| 项目 | 详情 |
|------|------|
| 🔗 注册 | [dashboard.cohere.com/api-keys](https://dashboard.cohere.com/api-keys) |
| 💳 需要信用卡 | ❌ 不需要 |
| 📍 Base URL | `https://api.cohere.com/v2` |
| 🔑 环境变量 | `COHERE_API_KEY` |
| 🆓 额度 | 1000 次/月（仅限非商业用途） |

### 免费模型

| 模型 | 上下文 | 最大输出 |
|------|--------|---------|
| `command-a-08-2025` (111B) | 256K | 8K |
| `command-r-08-2024` | 128K | 4K |
| `command-r-plus-08-2024` | 128K | 4K |
| `command-r7b-12-2024` | 128K | 4K |

> ⚠️ Cohere API 格式与 OpenAI 不同，需使用 Cohere SDK

---

## 8. SiliconFlow

| 项目 | 详情 |
|------|------|
| 🔗 注册 | [cloud.siliconflow.cn](https://cloud.siliconflow.cn/account/ak) |
| 💳 需要信用卡 | ❌ 不需要 |
| 📍 Base URL | `https://api.siliconflow.cn/v1` |
| 🔑 环境变量 | `SILICONFLOW_API_KEY` |
| 🇨🇳 特点 | 国内平台、中文优化 |
| ⚠️ 注意 | 需要实名认证（仅支持中国大陆身份证） |

### 免费模型

| 模型 | 上下文 | 速率限制 |
|------|--------|---------|
| `Qwen/Qwen3-8B` | 128K | 1000 RPM, 50000 TPM |
| `Qwen/Qwen2.5-7B-Instruct` | 128K | 1000 RPM |
| `THUDM/glm-4-9b-chat` | 128K | 1000 RPM |

### 调用示例

```python
import openai

client = openai.OpenAI(
    api_key="YOUR_SILICONFLOW_API_KEY",
    base_url="https://api.siliconflow.cn/v1",
)

response = client.chat.completions.create(
    model="Qwen/Qwen3-8B",
    messages=[
        {"role": "system", "content": "你是小忆，桌面猫咪AI助手喵~"},
        {"role": "user", "content": "你好！"},
    ],
    max_tokens=512,
    temperature=0.7,
)
print(response.choices[0].message.content)
```

---

## 9. Z AI (智谱)

| 项目 | 详情 |
|------|------|
| 🔗 注册 | [open.bigmodel.cn](https://open.bigmodel.cn/usercenter/apikeys) |
| 💳 需要信用卡 | ❌ 不需要 |
| 📍 Base URL | `https://open.bigmodel.cn/api/paas/v4` |
| 🔑 环境变量 | `ZHIPU_API_KEY` |
| 🇨🇳 特点 | 国内平台、GLM 系列模型 |
| ⚠️ 注意 | 免费模型仅支持 1 个并发请求 |

### 免费模型

| 模型 | 上下文 | 最大输出 | 模态 |
|------|--------|---------|------|
| `glm-4.7-flash` | 200K | 128K | Text (reasoning) |
| `glm-4.5-flash` | 128K | 96K | Text (reasoning) |
| `glm-4.6v-flash` | 128K | 32K | 多模态 |

### 调用示例

```python
from zhipuai import ZhipuAI

client = ZhipuAI(api_key="YOUR_ZHIPU_API_KEY")

response = client.chat.completions.create(
    model="glm-4.7-flash",
    messages=[
        {"role": "system", "content": "你是小忆，桌面猫咪AI助手喵~"},
        {"role": "user", "content": "你好！"},
    ],
    max_tokens=512,
    temperature=0.7,
)
print(response.choices[0].message.content)
```

---

## 10. OVHcloud (匿名免费)

| 项目 | 详情 |
|------|------|
| 🔗 注册 | [ovhcloud.com](https://www.ovhcloud.com/en/public-cloud/ai-endpoints/catalog/) |
| 💳 需要信用卡 | ❌ 不需要 |
| 💳 需要注册 | ❌ **不需要！匿名即可调用** |
| 📍 Base URL | `https://oai.endpoints.kepler.ai.cloud.ovh.net/v1` |
| 🔑 API Key | 无需（匿名） |
| ⚠️ 限制 | 2 RPM（每 IP 每模型） |

### 免费模型

| 模型 | 上下文 |
|------|--------|
| `Qwen3.5-397B-A17B` | 131K |
| `gpt-oss-120b` | 128K |
| `Meta-Llama-3_3-70B-Instruct` | 131K |
| `Qwen3.6-27B` | 131K |
| `Qwen3.5-9B` | 131K |
| `Mistral-Small-3.2-24B-Instruct` | 128K |

### 调用示例

```python
import openai

# 无需 API Key，直接调用
client = openai.OpenAI(
    api_key="dummy",  # OVHcloud 不验证
    base_url="https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
)

response = client.chat.completions.create(
    model="Qwen3.5-397B-A17B",
    messages=[
        {"role": "system", "content": "你是小忆，桌面猫咪AI助手喵~"},
        {"role": "user", "content": "你好！"},
    ],
    max_tokens=512,
    temperature=0.7,
)
print(response.choices[0].message.content)
```

---

## 11. Kilo Code

| 项目 | 详情 |
|------|------|
| 🔗 注册 | [app.kilo.ai](https://app.kilo.ai/profile) |
| 💳 需要信用卡 | ❌ 不需要 |
| 💳 需要 API Key | ❌ **不需要！** |
| 📍 Base URL | `https://api.kilo.ai/api/gateway` |
| 🔑 环境变量 | 无需 |
| ⚠️ 限制 | 200 req/hr（每 IP） |

### 免费模型

| 模型 | 上下文 | 最大输出 |
|------|--------|---------|
| `nvidia/nemotron-3-ultra-550b-a55b:free` | 1M | 65K |
| `stepfun/step-3.7-flash:free` | 262K | 262K |
| `nvidia/nemotron-3-super-120b-a12b:free` | 262K | 262K |
| `openrouter/free` | Varies | Varies |
| `nvidia/nemotron-3.5-lightning:free` | 1M | 65K |
| `tencent/hy3:free` | 262K | 128K |

### 调用示例

```python
import openai

# 无需 API Key
client = openai.OpenAI(
    api_key="dummy",
    base_url="https://api.kilo.ai/api/gateway",
)

response = client.chat.completions.create(
    model="nvidia/nemotron-3-ultra-550b-a55b:free",
    messages=[
        {"role": "system", "content": "你是小忆，桌面猫咪AI助手喵~"},
        {"role": "user", "content": "你好！"},
    ],
    max_tokens=512,
    temperature=0.7,
)
print(response.choices[0].message.content)
```

---

## 12. Hugging Face

| 项目 | 详情 |
|------|------|
| 🔗 注册 | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) |
| 💳 需要信用卡 | ❌ 不需要 |
| 📍 Base URL | `https://router.huggingface.co/v1` |
| 🔑 环境变量 | `HF_TOKEN` |
| 🆓 额度 | ~$0.10/月（信用额度） |

### 热门免费模型

| 模型 | 上下文 |
|------|--------|
| `Meta-Llama-3.1-8B-Instruct` | 128K |
| `gemma-3-4b-it` | 131K |
| `phi-4` | 16K |
| `Qwen2.5-Coder-7B-Instruct` | 131K |
| `Qwen2.5-7B-Instruct` | 131K |

### 调用示例

```python
import openai

client = openai.OpenAI(
    api_key="YOUR_HF_TOKEN",
    base_url="https://router.huggingface.co/v1",
)

response = client.chat.completions.create(
    model="Meta-Llama-3.1-8B-Instruct",
    messages=[
        {"role": "system", "content": "你是小忆，桌面猫咪AI助手喵~"},
        {"role": "user", "content": "你好！"},
    ],
    max_tokens=512,
    temperature=0.7,
)
print(response.choices[0].message.content)
```

---

## 附录：通用调用代码

### Python（使用 openai 库）

```python
import openai

def chat_with_llm(
    provider: str,
    api_key: str,
    model: str,
    user_message: str,
    system_prompt: str = "你是小忆，桌面猫咪AI助手喵~",
) -> str:
    """通用 LLM 对话函数"""
    
    PROVIDERS = {
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
        "groq": "https://api.groq.com/openai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "mistral": "https://api.mistral.ai/v1",
        "nvidia": "https://integrate.api.nvidia.com/v1",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "ovhcloud": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        "kilo": "https://api.kilo.ai/api/gateway",
        "huggingface": "https://router.huggingface.co/v1",
    }
    
    base_url = PROVIDERS.get(provider)
    if not base_url:
        raise ValueError(f"未知提供商: {provider}")
    
    client = openai.OpenAI(api_key=api_key, base_url=base_url)
    
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=512,
        temperature=0.7,
    )
    
    return response.choices[0].message.content
```

### 推荐配置（小忆桌面宠物）

```yaml
# config.yaml - 推荐配置
plugins:
  llm:
    engine: openrouter  # 插件名
    params:
      # 方案一：Google Gemini（推荐，额度最大）
      provider: gemini
      api_key: "YOUR_GOOGLE_API_KEY"
      model: gemini-2.0-flash
      base_url: https://generativelanguage.googleapis.com/v1beta/openai
      
      # 方案二：Groq（推荐，速度最快）
      # provider: groq
      # api_key: "YOUR_GROQ_API_KEY"
      # model: llama-3.1-8b-instant
      # base_url: https://api.groq.com/openai/v1
      
      # 方案三：OpenRouter（多模型选择）
      # provider: openrouter
      # api_key: "YOUR_OPENROUTER_API_KEY"
      # model: nvidia/nemotron-3-super-120b-a12b:free
      # base_url: https://openrouter.ai/api/v1
      
      # 方案四：OVHcloud（无需注册）
      # provider: ovhcloud
      # api_key: "dummy"
      # model: Qwen3.5-397B-A17B
      # base_url: https://oai.endpoints.kepler.ai.cloud.ovh.net/v1
      
      system_prompt: "你是小忆，一只可爱的桌面猫咪AI助手，住在用户的电脑桌面上。回答要简短亲切，多用语气词和猫叫（喵~），像一只会说话的小猫。用中文回答。"
      max_tokens: 512
      temperature: 0.7
```

---

## 免费额度速查表

| 提供商 | RPM | RPD | 每月上限 | 需要注册 | 需要信用卡 |
|--------|-----|-----|---------|---------|-----------|
| Google Gemini | 15 | 1,500 | — | ✅ | ❌ |
| Groq | 30 | 1,000 | — | ✅ | ❌ |
| OpenRouter | 20 | 50 | — | ✅ | ❌ |
| Mistral | ~60 | — | $10 额度 | ✅ | ❌ |
| NVIDIA NIM | 40 | 10,000 | — | ✅ | ❌ |
| Cloudflare | — | — | 10K Neurons | ✅ | ❌ |
| Cohere | 20 | — | 1,000 次 | ✅ | ❌ |
| SiliconFlow | 1000 | — | — | ✅+实名 | ❌ |
| Z AI (智谱) | 1 | — | — | ✅ | ❌ |
| OVHcloud | 2 | — | — | ❌ | ❌ |
| Kilo Code | ~3 | — | 200/hr | ❌ | ❌ |
| Hugging Face | — | — | ~$0.10 | ✅ | ❌ |

---

*数据来源：[awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis)*
