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

import CopyToClipboard from '@/components/copy-to-clipboard';
import { SelectWithSearch } from '@/components/originui/select-with-search';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { SharedFrom } from '@/constants/chat';
import {
  LanguageAbbreviation,
  LanguageAbbreviationMap,
  ThemeEnum,
} from '@/constants/common';
import { IModalProps } from '@/interfaces/common';
import { Routes } from '@/routes';
import { zodResolver } from '@hookform/resolvers/zod';
import { isEmpty, trim } from 'lodash';
import { ExternalLink } from 'lucide-react';
import { memo, useCallback, useMemo } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import {
  oneDark,
  oneLight,
} from 'react-syntax-highlighter/dist/esm/styles/prism';
import { z } from 'zod';
import { RAGFlowFormItem } from '../ragflow-form';
import { SwitchFormField } from '../switch-fom-field';
import { useIsDarkTheme } from '../theme-provider';
import { Input } from '../ui/input';

const MAX_EMBED_USER_ID_LENGTH = 255;

const FormSchema = z.object({
  visibleAvatar: z.boolean(),
  published: z.boolean(),
  locale: z.string(),
  enableStreaming: z.boolean(),
  muteWidget: z.boolean(),
  theme: z.enum([ThemeEnum.Light, ThemeEnum.Dark]),
  userId: z.string().max(MAX_EMBED_USER_ID_LENGTH).optional(),
  widgetTitle: z.string(),
  widgetSubtitle: z.string(),
  widgetFooterText: z.string(),
  widgetFooterLink: z.string(),
  widgetAccentColor: z.string(),
  widgetBackgroundColor: z.string(),
  widgetTextColor: z.string(),
  widgetHeaderTextColor: z.string(),
  widgetFooterTextColor: z.string(),
});

export type WidgetSettings = Pick<
  z.infer<typeof FormSchema>,
  | 'enableStreaming'
  | 'muteWidget'
  | 'widgetTitle'
  | 'widgetSubtitle'
  | 'widgetFooterText'
  | 'widgetFooterLink'
  | 'widgetAccentColor'
  | 'widgetBackgroundColor'
  | 'widgetTextColor'
  | 'widgetHeaderTextColor'
  | 'widgetFooterTextColor'
>;

export const defaultWidgetSettings: WidgetSettings = {
  enableStreaming: false,
  muteWidget: false,
  widgetTitle: '',
  widgetSubtitle: '',
  widgetFooterText: '',
  widgetFooterLink: '',
  widgetAccentColor: '#2563eb',
  widgetBackgroundColor: '#ffffff',
  widgetTextColor: '#111827',
  widgetHeaderTextColor: '#ffffff',
  widgetFooterTextColor: '#111827',
};

type IProps = IModalProps<any> & {
  token: string;
  from: SharedFrom;
  beta: string;
  isAgent: boolean;
  initialWidgetSettings?: Partial<WidgetSettings>;
  onSaveWidgetSettings?: (settings: WidgetSettings) => Promise<unknown>;
  savingWidgetSettings?: boolean;
};

/**
 * Builds the embed code preview and customization UI for shared agents.
 */
function EmbedDialog({
  hideModal,
  token = '',
  from,
  beta = '',
  isAgent,
  visible,
}: IProps) {
  const { t } = useTranslation();
  const isDarkTheme = useIsDarkTheme();

  const form = useForm<z.infer<typeof FormSchema>>({
    resolver: zodResolver(FormSchema),
    defaultValues: {
      visibleAvatar: false,
      published: false,
      locale: '',
      theme: ThemeEnum.Light,
      ...defaultWidgetSettings,
    },
  });

  const values = useWatch({ control: form.control });

  const languageOptions = useMemo(() => {
    return Object.values(LanguageAbbreviation).map((x) => ({
      label: LanguageAbbreviationMap[x],
      value: x,
    }));
  }, []);

  const generateIframeSrc = useCallback(() => {
    const { visibleAvatar, published, locale, theme, userId } = values;
    const baseRoute = Routes.AgentShare;

    const src = new URL(`${location.origin}${baseRoute}`);
    src.searchParams.append('shared_id', token);
    src.searchParams.append('from', from);
    src.searchParams.append('auth', beta);

    if (published) {
      src.searchParams.append('release', 'true');
    }
    if (visibleAvatar) {
      src.searchParams.append('visible_avatar', '1');
    }
    if (locale) {
      src.searchParams.append('locale', locale);
    }
    if (theme) {
      src.searchParams.append('theme', theme);
    }
    if (!isEmpty(trim(userId))) {
      src.searchParams.append('userId', userId!);
    }

    return src.toString();
  }, [beta, from, token, values]);

  const text = useMemo(() => {
    const iframeSrc = generateIframeSrc();
    return `<iframe
  src="${iframeSrc}"
  style="width: 100%; height: 100%; min-height: 600px"
  frameborder="0"
></iframe>
`;
  }, [generateIframeSrc]);

  const handleOpenInNewTab = useCallback(() => {
    const iframeSrc = generateIframeSrc();
    window.open(iframeSrc, '_blank');
  }, [generateIframeSrc]);

  return (
    <Dialog open={visible} onOpenChange={hideModal}>
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{t('common.embedIntoSite')}</DialogTitle>
        </DialogHeader>

        <section className="w-full overflow-auto space-y-5 text-sm text-text-secondary">
          <Form {...form}>
            <form className="space-y-5">
              <FormField
                control={form.control}
                name="theme"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t('chat.theme')}</FormLabel>
                    <FormControl>
                      <RadioGroup
                        onValueChange={field.onChange}
                        value={field.value}
                        className="flex flex-row space-x-4"
                      >
                        <div className="flex items-center space-x-2">
                          <RadioGroupItem value={ThemeEnum.Light} id="light" />
                          <Label htmlFor="light" className="text-sm">
                            {t('chat.light')}
                          </Label>
                        </div>
                        <div className="flex items-center space-x-2">
                          <RadioGroupItem value={ThemeEnum.Dark} id="dark" />
                          <Label htmlFor="dark" className="text-sm">
                            {t('chat.dark')}
                          </Label>
                        </div>
                      </RadioGroup>
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <SwitchFormField
                name="visibleAvatar"
                label={t('chat.avatarHidden')}
              ></SwitchFormField>
              {isAgent && (
                <SwitchFormField
                  name="published"
                  label={t('chat.published')}
                  tooltip={t('chat.publishedTooltip')}
                ></SwitchFormField>
              )}
              <RAGFlowFormItem name="locale" label={t('chat.locale')}>
                <SelectWithSearch options={languageOptions}></SelectWithSearch>
              </RAGFlowFormItem>
              {isAgent && (
                <RAGFlowFormItem
                  name="userId"
                  label={t('flow.userId')}
                  tooltip={t('chat.embedUserIdTooltip')}
                >
                  <Input
                    maxLength={MAX_EMBED_USER_ID_LENGTH}
                    placeholder={t('chat.embedUserIdPlaceholder')}
                  ></Input>
                </RAGFlowFormItem>
              )}
            </form>
          </Form>
          <div>
            <span>{t('search.embedCode')}</span>
            <div className="relative">
              <CopyToClipboard
                text={text}
                className="absolute right-3 top-3 z-10 border border-border bg-background/90 backdrop-blur-sm"
              />
              <SyntaxHighlighter
                className="max-h-[350px] overflow-auto scrollbar-auto pr-14"
                language="html"
                style={isDarkTheme ? oneDark : oneLight}
              >
                {text}
              </SyntaxHighlighter>
            </div>
          </div>
          <div className="flex gap-3">
            <Button
              onClick={handleOpenInNewTab}
              className="w-full"
              variant="secondary"
            >
              <ExternalLink className="mr-2 h-4 w-4" />
              {t('common.openInNewTab')}
            </Button>
          </div>
          <div className=" font-medium mt-4 mb-1">
            {t('flow', { keyPrefix: 'header' })}
            <span className="ml-1 inline-block">ID</span>
          </div>
          <div className="bg-bg-card rounded-lg flex items-center justify-between p-2">
            <span>{token} </span>
            <CopyToClipboard text={token}></CopyToClipboard>
          </div>
          <a
            className="cursor-pointer text-accent-primary inline-block"
            href={
              'https://ragflow.io/docs/dev/http_api_reference#create-session-with-agent'
            }
            target="_blank"
            rel="noreferrer"
          >
            {t('flow.howUseId')}
          </a>
        </section>
      </DialogContent>
    </Dialog>
  );
}

export default memo(EmbedDialog);
