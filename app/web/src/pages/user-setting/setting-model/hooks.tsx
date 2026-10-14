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


import { useFetchAddedProviders, useFetchProviderInstances, useVerifyProviderConnection } from '@/hooks/use-llm-request';
import { IModelInfo } from '@/interfaces/request/llm';
import { useCallback, useMemo } from 'react';


export type VerifyResult = {
  isValid: boolean | null;
  logs: string;
};

export const useFetchInstanceNameSet = (providerName: string) => {
  const { data: addedProviders } = useFetchAddedProviders();
  const providerExists = useMemo(
    () => addedProviders.some((p) => p.name === providerName),
    [addedProviders, providerName],
  );
  const { data: instances } = useFetchProviderInstances(
    providerExists ? providerName : '',
  );
  const instanceNameSet = useMemo(
    () => new Set(instances.map((i) => i.instance_name)),
    [instances],
  );
  return { instanceNameSet, providerExists };
};

export const useHideWhenInstanceExists = (instanceNameSet: Set<string>) => {
  return useCallback(
    (formValues: any) => {
      const name = ((formValues?.instance_name as string) || '').trim();
      return !(name && instanceNameSet.has(name));
    },
    [instanceNameSet],
  );
};

export const useVerifyConnection = () => {
  const { verifyProviderConnection } = useVerifyProviderConnection();

  return useCallback(
    async (
      providerName: string,
      apiKey: string,
      baseUrl?: string,
      region?: string,
      modelInfo?: IModelInfo[],
    ) => {
      const ret = await verifyProviderConnection({
        provider_name: providerName,
        api_key: apiKey,
        base_url: baseUrl,
        region: region,
        model_info: modelInfo,
      });

      if (ret.code === 0) {
        return {
          isValid: true,
          logs: ret.message,
        } as VerifyResult;
      } else {
        return {
          isValid: false,
          logs: ret.message,
        } as VerifyResult;
      }
    },
    [verifyProviderConnection],
  );
};
