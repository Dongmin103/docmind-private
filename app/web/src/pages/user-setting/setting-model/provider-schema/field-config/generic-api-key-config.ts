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
import type { ProviderConfig } from '../types';

export const GenericApiKeyConfig: ProviderConfig = {
  llmFactory: '__generic__',
  title: 'API Key',
  fields: [
    {
      name: 'instance_name', label: 'instanceName', type: FormFieldType.Text,
      required: true, placeholder: 'instanceNameMessage', tooltip: 'instanceNameTip',
      autoComplete: 'new-password', validation: { message: 'instanceNameMessage' },
    },
    {
      name: 'api_key', label: 'apiKey', type: FormFieldType.Password,
      required: true, placeholder: 'apiKeyMessage', autoComplete: 'new-password',
      validation: { message: 'apiKeyMessage' },
    },
    {
      name: 'base_url', label: 'baseUrl', type: 'inputSelect', required: false,
      tooltip: 'baseUrlTip', placeholder: 'openaiBaseUrlPlaceholder',
      shouldRender: 'showBaseUrl', autoComplete: 'new-password',
    },
  ],
  verifyTransform: (values) => ({ apiKey: values.api_key, baseUrl: values.base_url }),
  submitTransform: (values) => ({
    instance_name: values.instance_name,
    api_key: values.api_key ?? '',
    base_url: values.base_url || '',
  }),
};
