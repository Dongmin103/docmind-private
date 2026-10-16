/*
 *  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
 *
 *  Licensed under the Apache License, Version 2.0 (the "License");
 *  you may not use this file except in compliance with the License.
 *  You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 *  Unless required by applicable law or agreed to in writing, software
 *  distributed under the License is distributed on an "AS IS" BASIS,
 *  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *  See the License for the specific language governing permissions and
 *  limitations under the License.
 */

import { FormFieldType } from '@/components/dynamic-form';
import { LLMFactory } from '@/constants/llm';
import type { FieldConfig, ProviderConfig } from '../types';
import { buildModelInfoFromValues } from './utils';

/**
 * Factory configuration for local/compatible factories
 */
export const LocalLlmConfigs: Record<string, ProviderConfig> = {
  [LLMFactory.OpenAiAPICompatible]: buildLocalConfig(
    LLMFactory.OpenAiAPICompatible,
    'OpenAiAPICompatible',
    false,
    undefined,
    'https://platform.openai.com/docs/models/gpt-4',
  ),


};

/**
 * Build the default configuration for local factories
 */
function buildLocalConfig(
  llmFactory: string,
  title: string,
  addProviderOrder = false,
  customFields?: FieldConfig[],
  docLink?: string,
): ProviderConfig {
  const fields: FieldConfig[] = [
    {
      name: 'instance_name',
      label: 'instanceName',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'instanceNameMessage',
      tooltip: 'instanceNameTip',
    },
    // {
    //   name: 'model_type',
    //   label: 'modelType',
    //   type: FormFieldType.MultiSelect,
    //   required: true,
    //   options: modelTypes.map((t) => ({ label: capitalize(t), value: t })),
    // },
    // {
    //   name: 'model_name',
    //   label: modelNameLabel ?? 'modelName',
    //   type: FormFieldType.Text,
    //   required: true,
    //   placeholder: 'modelNameMessage',
    // },
    {
      name: 'base_url',
      label: 'addLlmBaseUrl',
      type: 'inputSelect',
      required: true,
      placeholder: 'baseUrlNameMessage',
      autoComplete: 'new-password',
      shouldRender: 'hideWhenInstanceExists',
    },
    {
      name: 'api_key',
      label: 'apiKey',
      type: FormFieldType.Password,
      required: false,
      placeholder: 'apiKeyMessage',
      autoComplete: 'new-password',
      shouldRender: 'hideWhenInstanceExists',
    },
    // {
    //   name: 'max_tokens',
    //   label: 'maxTokens',
    //   type: FormFieldType.Number,
    //   required: true,
    //   placeholder: 'maxTokensTip',
    //   defaultValue: 8192,
    //   validation: { min: 0, message: 'maxTokensMessage' },
    // },
    // {
    //   name: 'is_tools',
    //   label: 'enableToolCall',
    //   type: FormFieldType.Switch,
    //   required: false,
    //   shouldRender: 'modelTypeSupportsToolCall',
    //   defaultValue: false,
    // },
  ];

  if (addProviderOrder) {
    fields.push({
      name: 'provider_order',
      label: 'providerOrder',
      type: FormFieldType.Text,
      required: false,
    });
  }

  fields.push({
    name: 'vision',
    label: 'vision',
    type: FormFieldType.Switch,
    required: false,
    defaultValue: false,
    shouldRender: 'modelTypeIncludesChat',
  });

  const customFieldMap = new Map((customFields ?? []).map((f) => [f.name, f]));
  const mergedFields = fields
    .map((f) => customFieldMap.get(f.name) ?? f)
    .concat(
      (customFields ?? []).filter(
        (f) => !fields.some((ef) => ef.name === f.name),
      ),
    );
  return {
    llmFactory,
    title,
    fields: mergedFields,
    ...(docLink ? { docLink, docLinkI18nKey: 'ollamaLink' } : {}),
    verifyTransform: (values) => ({
      apiKey: values.api_key || '',
      baseUrl: values.base_url,
      modelInfo: buildModelInfoFromValues(values),
    }),
    submitTransform: (values) => {
      const apiKey = values.provider_order
        ? {
            api_key: values.api_key ?? '',
            provider_order: values.provider_order,
          }
        : (values.api_key ?? '');
      return {
        instance_name: values.instance_name,
        llm_factory: llmFactory,
        model_info: buildModelInfoFromValues(values),
        base_url: values.base_url,
        api_key: apiKey,
      };
    },
  };
}
