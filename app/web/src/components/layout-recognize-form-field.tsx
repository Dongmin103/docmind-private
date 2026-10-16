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

import { useTranslate } from '@/hooks/common-hooks';
import { cn } from '@/lib/utils';
import { camelCase } from 'lodash';
import { ReactNode, useMemo } from 'react';
import { useFormContext } from 'react-hook-form';

import { TreeSelect, TreeSelectNode } from './tree-select';
import { FormControl, FormField, FormItem, FormLabel, FormMessage } from './ui/form';

export const enum ParseDocumentType {
  DeepDOC = 'DeepDOC',
  PlainText = 'Plain Text',
}

export function LayoutRecognizeFormField({
  name = 'parser_config.layout_recognize',
  horizontal = true,
  optionsWithoutLLM,
  label,
  testId,
}: {
  name?: string;
  horizontal?: boolean;
  optionsWithoutLLM?: { value: string; label: string }[];
  label?: ReactNode;
  testId?: string;
  ownerTenantId?: string;
}) {
  const form = useFormContext();

  const { t } = useTranslate('knowledgeDetails');
  const treeData = useMemo(() => {
    const list = optionsWithoutLLM
      ? optionsWithoutLLM
      : [
          ParseDocumentType.DeepDOC,
          ParseDocumentType.PlainText,
        ].map((x) => ({
          label: x === ParseDocumentType.PlainText ? t(camelCase(x)) : x,
          value: x,
        }));

    const prependNodes: TreeSelectNode[] = list.map((x) => ({
      id: x.value,
      title: x.label,
    }));

    return prependNodes;
  }, [optionsWithoutLLM, t]);

  return (
    <FormField
      control={form.control}
      name={name}
      render={({ field }) => {
        return (
          <>
            <FormItem className={'items-center space-y-0 '}>
              <div
                className={cn('flex', {
                  'flex-col ': !horizontal,
                  'items-center': horizontal,
                })}
              >
                <FormLabel
                  tooltip={t('layoutRecognizeTip')}
                  className={cn('text-sm text-text-secondary whitespace-wrap', {
                    ['w-1/4']: horizontal,
                  })}
                >
                  {label || t('layoutRecognize')}
                </FormLabel>
                <div className={horizontal ? 'w-3/4' : 'w-full'}>
                  <FormControl>
                    <TreeSelect
                      {...field}
                      data={treeData}
                      testId={testId}
                      showSearch
                      defaultExpandAll

                      renderSelected={(node) => {
                        if (!node) return null;
                        return node.label ?? node.title;
                      }}
                    />
                  </FormControl>
                </div>
              </div>
              <div className="flex pt-1">
                <div className={horizontal ? 'w-1/4' : 'w-full'}></div>
                <FormMessage />
              </div>
            </FormItem>
          </>
        );
      }}
    />
  );
}
