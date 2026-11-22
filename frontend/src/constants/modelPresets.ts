import type { ModelType } from '@/types/llm'

export type ProviderGroupKey = 'global' | 'china' | 'gateway' | 'local'

export interface ModelProviderItem {
  id: string
  group: ProviderGroupKey
  label: string
  base_url: string
  examples: Partial<Record<ModelType, string>>
}

export const MODEL_PROVIDERS: ModelProviderItem[] = [
  // --- Global Cloud ---
  {
    id: 'openai',
    group: 'global',
    label: 'OpenAI',
    base_url: 'https://api.openai.com/v1',
    examples: {
      chat: 'gpt-4o / gpt-4.1 / o3-mini',
      embedding: 'text-embedding-3-large / text-embedding-3-small',
    },
  },
  {
    id: 'google',
    group: 'global',
    label: 'Google Gemini (OpenAI Compat)',
    base_url: 'https://generativelanguage.googleapis.com/v1beta/openai',
    examples: {
      chat: 'gemini-2.5-pro / gemini-2.5-flash',
      embedding: 'text-embedding-004',
    },
  },
  {
    id: 'anthropic_compat',
    group: 'global',
    label: 'Anthropic Claude (OpenAI Compat)',
    base_url: 'https://api.anthropic.com/v1',
    examples: {
      chat: 'claude-sonnet-4-20250514 / claude-3-7-sonnet-latest',
    },
  },
  {
    id: 'xai',
    group: 'global',
    label: 'xAI (Grok)',
    base_url: 'https://api.x.ai/v1',
    examples: {
      chat: 'grok-3 / grok-3-mini',
    },
  },
  {
    id: 'groq',
    group: 'global',
    label: 'Groq',
    base_url: 'https://api.groq.com/openai/v1',
    examples: {
      chat: 'llama-3.3-70b-versatile / deepseek-r1-distill-llama-70b',
    },
  },
  {
    id: 'mistral',
    group: 'global',
    label: 'Mistral AI',
    base_url: 'https://api.mistral.ai/v1',
    examples: {
      chat: 'mistral-large-latest / codestral-latest',
      embedding: 'mistral-embed',
    },
  },
  {
    id: 'cohere',
    group: 'global',
    label: 'Cohere',
    base_url: 'https://api.cohere.com/v2',
    examples: {
      chat: 'command-a-03-2025',
      embedding: 'embed-multilingual-v3.0',
      rerank: 'rerank-v3.5',
    },
  },

  // --- China Cloud ---
  {
    id: 'deepseek',
    group: 'china',
    label: 'DeepSeek (深度求索)',
    base_url: 'https://api.deepseek.com/v1',
    examples: {
      chat: 'deepseek-chat / deepseek-reasoner',
    },
  },
  {
    id: 'dashscope',
    group: 'china',
    label: '阿里百炼 DashScope (通义千问)',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    examples: {
      chat: 'qwen-max / qwen-plus / qwen3-coder-plus',
      embedding: 'text-embedding-v3',
      rerank: 'gte-rerank',
    },
  },
  {
    id: 'zhipu',
    group: 'china',
    label: '智谱 AI BigModel (GLM)',
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    examples: {
      chat: 'glm-4.5 / glm-4-plus',
      embedding: 'embedding-3',
    },
  },
  {
    id: 'moonshot',
    group: 'china',
    label: '月之暗面 Moonshot (Kimi)',
    base_url: 'https://api.moonshot.cn/v1',
    examples: {
      chat: 'kimi-k2-0711-preview / moonshot-v1-128k',
    },
  },
  {
    id: 'volcengine',
    group: 'china',
    label: '火山引擎方舟 Volcengine (豆包)',
    base_url: 'https://ark.cn-beijing.volces.com/api/v3',
    examples: {
      chat: 'doubao-1-5-pro-32k-250115 / deepseek-r1-250120',
      embedding: 'doubao-embedding-large-text-240915',
    },
  },
  {
    id: 'siliconflow',
    group: 'china',
    label: '硅基流动 SiliconFlow',
    base_url: 'https://api.siliconflow.cn/v1',
    examples: {
      chat: 'deepseek-ai/DeepSeek-V3 / Qwen/Qwen2.5-Coder-32B-Instruct',
      embedding: 'BAAI/bge-m3',
      rerank: 'BAAI/bge-reranker-v2-m3',
    },
  },
  {
    id: 'minimax',
    group: 'china',
    label: 'MiniMax',
    base_url: 'https://api.minimax.chat/v1',
    examples: {
      chat: 'MiniMax-Text-01',
    },
  },
  {
    id: 'qianfan',
    group: 'china',
    label: '百度千帆 Qianfan (文心)',
    base_url: 'https://qianfan.baidubce.com/v2',
    examples: {
      chat: 'ernie-4.5-turbo-128k',
      embedding: 'bge-large-zh',
    },
  },
  {
    id: 'hunyuan',
    group: 'china',
    label: '腾讯混元 Hunyuan',
    base_url: 'https://api.hunyuan.cloud.tencent.com/v1',
    examples: {
      chat: 'hunyuan-turbos-latest',
      embedding: 'hunyuan-embedding',
    },
  },

  // --- Gateways & Aggregators ---
  {
    id: 'openrouter',
    group: 'gateway',
    label: 'OpenRouter',
    base_url: 'https://openrouter.ai/api/v1',
    examples: {
      chat: 'anthropic/claude-sonnet-4 / google/gemini-2.5-pro',
    },
  },
  {
    id: 'together',
    group: 'gateway',
    label: 'Together AI',
    base_url: 'https://api.together.xyz/v1',
    examples: {
      chat: 'meta-llama/Llama-3.3-70B-Instruct-Turbo',
      embedding: 'BAAI/bge-large-en-v1.5',
      rerank: 'Salesforce/Llama-Rank-V1',
    },
  },
  {
    id: 'fireworks',
    group: 'gateway',
    label: 'Fireworks AI',
    base_url: 'https://api.fireworks.ai/inference/v1',
    examples: {
      chat: 'accounts/fireworks/models/deepseek-v3',
    },
  },
  {
    id: 'jina',
    group: 'gateway',
    label: 'Jina AI',
    base_url: 'https://api.jina.ai/v1',
    examples: {
      embedding: 'jina-embeddings-v3',
      rerank: 'jina-reranker-v2-base-multilingual',
    },
  },
  {
    id: 'newapi',
    group: 'gateway',
    label: 'One API / New API (自建中转)',
    base_url: 'http://localhost:3000/v1',
    examples: {
      chat: 'gpt-4o / claude-sonnet-4 / deepseek-chat',
      embedding: 'text-embedding-3-small',
      rerank: 'bge-reranker-v2-m3',
    },
  },

  // --- Local / Self-Hosted ---
  {
    id: 'ollama',
    group: 'local',
    label: 'Ollama (本地)',
    base_url: 'http://localhost:11434/v1',
    examples: {
      chat: 'qwen2.5-coder:14b / deepseek-r1:14b',
      embedding: 'nomic-embed-text / bge-m3',
    },
  },
  {
    id: 'vllm',
    group: 'local',
    label: 'vLLM (私有化部署)',
    base_url: 'http://localhost:8001/v1',
    examples: {
      chat: 'Qwen/Qwen2.5-Coder-32B-Instruct',
    },
  },
  {
    id: 'lmstudio',
    group: 'local',
    label: 'LM Studio (本地)',
    base_url: 'http://localhost:1234/v1',
    examples: {
      chat: 'local-model',
    },
  },
  {
    id: 'xinference',
    group: 'local',
    label: 'Xinference (本地推理)',
    base_url: 'http://localhost:9997/v1',
    examples: {
      chat: 'qwen2.5-instruct',
      embedding: 'bge-m3',
      rerank: 'bge-reranker-v2-m3',
    },
  },
  {
    id: 'openai_compat',
    group: 'local',
    label: 'Custom OpenAI-Compatible (自定义兼容接口)',
    base_url: '',
    examples: {
      chat: 'your-chat-model-id',
      embedding: 'your-embedding-model-id',
      rerank: 'your-rerank-model-id',
    },
  },
]

export function findProviderById(id: string): ModelProviderItem | undefined {
  return MODEL_PROVIDERS.find((p) => p.id === id)
}
