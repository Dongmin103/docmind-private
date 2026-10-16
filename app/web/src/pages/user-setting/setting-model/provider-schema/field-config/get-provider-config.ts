import { LLMFactory } from '@/constants/llm';
import type { ProviderConfig } from '../types';
import { GenericApiKeyConfig } from './generic-api-key-config';
import { LocalLlmConfigs } from './local-llm-configs';

export function getProviderConfig(llmFactory: string): ProviderConfig {
  if (![LLMFactory.OpenAI, LLMFactory.OpenAiAPICompatible, LLMFactory.Jina].includes(llmFactory as LLMFactory)) {
    throw new Error(`Unsupported provider: ${llmFactory}`);
  }
  return LocalLlmConfigs[llmFactory] ?? { ...GenericApiKeyConfig, llmFactory };
}
