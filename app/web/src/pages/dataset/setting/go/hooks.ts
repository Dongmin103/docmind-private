import { Operator } from '@/constants/agent';
import { ParseType } from '@/constants/knowledge';
import {
  useFetchDatasetPipelineConfiguration,
  useUpdateKnowledge,
} from '@/hooks/use-knowledge-request';
import { checkEmbedding } from '@/services/knowledge-service';
import {
  getOperatorType,
  transformApiConfigToForm,
  transformFormConfigToApi,
} from '@/utils/pipeline-operator';
import { pick } from 'lodash';
import { useCallback, useEffect } from 'react';
import { UseFormReturn } from 'react-hook-form';
import { useParams, useSearchParams } from 'react-router';
import { z } from 'zod';
import { formSchema } from './form-schema';

export function useHasParsedDocument(isEdit?: boolean) {
  const { data: knowledgeDetails } = useFetchDatasetPipelineConfiguration({
    isEdit,
  });
  return knowledgeDetails.chunk_count > 0;
}

export const useHandleKbEmbedding = () => {
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const knowledgeBaseId = searchParams.get('id') || id;
  const handleChange = useCallback(
    async ({ embed_id }: { embed_id: string }) => {
      const res = await checkEmbedding(knowledgeBaseId || '', {
        embd_id: embed_id,
      });
      return res.data;
    },
    [knowledgeBaseId],
  );
  return {
    handleChange,
  };
};

export const useFetchDatasetSettingOnMount = (
  form: UseFormReturn<z.infer<typeof formSchema>>,
) => {
  const { data: knowledgeDetails, loading } =
    useFetchDatasetPipelineConfiguration();
  useEffect(() => {
    const parserConfig = knowledgeDetails.parser_config as
      | Record<string, any>
      | undefined;
    let formParserConfig: Record<string, any> | undefined = parserConfig;

    // Convert parser_config to form format if in pipeline mode
    if (
      parserConfig &&
      typeof parserConfig === 'object' &&
      !Array.isArray(parserConfig)
    ) {
      const keys = Object.keys(parserConfig);
      const hasPipelineKeys = keys.some((key) => key.includes(':'));
      if (hasPipelineKeys) {
        formParserConfig = {};
        for (const [operatorId, config] of Object.entries(parserConfig)) {
          const operatorType = getOperatorType(operatorId);
          formParserConfig[operatorId] = transformApiConfigToForm(
            operatorType,
            config as Record<string, any>,
          );
        }
      }
    }

    const formValues = {
      ...pick(knowledgeDetails, [
        'description',
        'name',
        'permission',
        'language',
        'pagerank',
        'avatar',
        'pipeline_id',
        'pipeline_name',
        'pipeline_avatar',
        'parser_id',
      ]),
      embedding_model: knowledgeDetails.embedding_model,
      parse_type: knowledgeDetails.pipeline_id
        ? ParseType.Pipeline
        : ParseType.BuiltIn,
      parser_config: formParserConfig,
    } as z.infer<typeof formSchema>;
    form.reset(formValues);
  }, [form, knowledgeDetails]);

  return { knowledgeDetails, loading };
};

export const useSaveDatasetSetting = () => {
  const { saveKnowledgeConfiguration, loading } = useUpdateKnowledge();

  const handleSave = useCallback(
    async (values: z.infer<typeof formSchema>) => {
      const payload = { ...values };

      // Apply forward transforms to parser_config if in pipeline mode
      if (payload.parser_config) {
        const transformedConfig: Record<string, any> = {};
        let extractorMetadataGroup: Record<string, any> | undefined;
        for (const [operatorId, config] of Object.entries(
          payload.parser_config,
        )) {
          const operatorType = getOperatorType(operatorId);
          const transformed = transformFormConfigToApi(
            operatorType,
            config as Record<string, any>,
          );
          transformedConfig[operatorId] = transformed;
          if (
            operatorType === Operator.Extractor &&
            extractorMetadataGroup === undefined
          ) {
            extractorMetadataGroup = transformed?.metadata;
          }
        }
        // parser_config.metadata is the dataset-level object the backend
        // preserves and re-scopes into every Extractor node. The extractor
        // tab is the only place the user edits it, so lift its metadata
        // group to the top level — otherwise the stale copy loaded with the
        // form would silently revert the toggle the user just set.
        if (extractorMetadataGroup) {
          transformedConfig.metadata = extractorMetadataGroup;
        }
        payload.parser_config = transformedConfig;
      }

      if (payload.parse_type === ParseType.BuiltIn) {
        payload.pipeline_id = undefined;
      } else {
        payload.parser_id = undefined;
      }
      return saveKnowledgeConfiguration(payload);
    },
    [saveKnowledgeConfiguration],
  );

  return { handleSave, loading };
};
