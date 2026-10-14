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

import { cn } from '@/lib/utils';
import { t } from 'i18next';
import { useIsDarkTheme } from '../theme-provider';

import { useMemo } from 'react';
import SvgIcon from '../svg-icon';
import { EmptyType } from './constant';
import { EmptyProps } from './interface';

const EmptyIcon = ({ name, width }: { name: string; width?: number }) => {
  return <SvgIcon name={name || 'empty/no-data-dark'} width={width || 42} />;
};

const Empty = (props: EmptyProps) => {
  const { className, children, type, text, iconWidth } = props;
  const isDarkTheme = useIsDarkTheme();

  const name = useMemo(() => {
    return isDarkTheme
      ? `empty/no-${type || EmptyType.Data}-dark`
      : `empty/no-${type || EmptyType.Data}-bri`;
  }, [isDarkTheme, type]);

  return (
    <div
      className={cn(
        'flex flex-col justify-center items-center text-center gap-2',
        className,
      )}
    >
      <EmptyIcon name={name} width={iconWidth} />

      {!children && (
        <div className="empty-text text-text-secondary text-sm">
          {text ||
            (type === 'data' ? t('common.noData') : t('common.noResults'))}
        </div>
      )}
      {children}
    </div>
  );
};

export default Empty;
