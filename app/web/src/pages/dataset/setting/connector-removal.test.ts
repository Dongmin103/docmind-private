import { ParseType } from '@/constants/knowledge';
import { formSchema as goSchema } from './go/form-schema';
import { formSchema as pythonSchema } from './python/form-schema';

it.each([
  ['go', goSchema],
  ['python', pythonSchema],
] as const)(
  '%s dataset settings omit retired connector bindings from submissions',
  (_, schema) => {
    const values = schema.parse({
      name: 'Documents',
      embedding_model: 'gte-multilingual-base',
      chunk_method: 'naive',
      parse_type: ParseType.BuiltIn,
      pagerank: 0,
      connectors: [{ id: 'retired-connector', source: 's3', auto_parse: '1' }],
    });
    expect(values).not.toHaveProperty('connectors');
    expect(values).toMatchObject({
      name: 'Documents',
      embedding_model: 'gte-multilingual-base',
      parse_type: ParseType.BuiltIn,
    });
  },
);
