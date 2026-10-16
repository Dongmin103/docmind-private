import { LLMFactory } from '@/constants/llm';
import { getProviderConfig } from './get-provider-config';
import { LIST_MODEL_PROVIDERS } from '../constants';

jest.mock('@/components/dynamic-form', () => ({ FormFieldType: { Text: 'text', Password: 'password', Switch: 'switch' } }));

it('supports only usable providers and the compatible model picker', () => {
  expect(Object.values(LLMFactory)).toEqual(['OpenAI', 'Jina', 'OpenAI-API-Compatible']);
  expect([...LIST_MODEL_PROVIDERS]).toEqual(['OpenAI-API-Compatible']);
  expect(getProviderConfig(LLMFactory.OpenAiAPICompatible).fields.find(field => field.name === 'base_url')?.required).toBe(true);
  expect(getProviderConfig(LLMFactory.Jina).fields.some(field => field.name === 'api_key')).toBe(true);
  expect(() => getProviderConfig('Bedrock')).toThrow('Unsupported provider');
});
import { renderHook } from '@testing-library/react';
import { useCustomModelFields } from '../../instance-card/use-custom-model-fields';
jest.mock('@/hooks/common-hooks', () => ({ useTranslate: () => ({ t: (value: string) => value }) }));

it.each([['Jina', ['rerank']], ['OpenAI-API-Compatible', ['chat', 'embedding', 'vision']]])('renders only supported custom model types for %s', (provider, expected) => {
  const { result } = renderHook(() => useCustomModelFields(String(provider)));
  expect(result.current.find(field => field.name === 'model_types')?.options?.map(option => option.value)).toEqual(expected);
});
