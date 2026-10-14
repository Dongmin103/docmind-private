export interface IDocumentInfo {
  // chunk_num: number;
  create_date: string;
  create_time: number;
  created_by: string;
  nickname: string;
  id: string;
  dataset_id: string;
  location: string;
  name: string;
  parser_config: IParserConfig;
  // parser_id: string;
  pipeline_id: string;
  pipeline_name: string;
  process_begin_at?: string;
  process_duration: number;
  progress: number;
  progress_msg: string;
  run: RunningStatus;
  size: number;
  source_type: string;
  status: string;
  suffix: string;
  thumbnail: string;
  token_num: number;
  type: string;
  update_date: string;
  update_time: number;
  meta_fields?: Record<string, any>;
  chunk_method: string;
  chunk_count: number;
}

export interface IParserConfig {
  delimiter?: string;
  html4excel?: boolean;
  layout_recognize?: string;
  pages?: any[];
  chunk_token_num?: number;
  auto_keywords?: number;
  auto_questions?: number;
  task_page_size?: number;
  image_context_window?: number;
  image_table_context_window?: number;
  image_context_size?: number;
  table_context_size?: number;
  mineru_parse_method?: 'auto' | 'txt' | 'ocr';
  mineru_formula_enable?: boolean;
  mineru_table_enable?: boolean;
  mineru_lang?: string;
  entity_types?: string[];
  metadata?: Array<{
    key?: string;
    description?: string;
    enum?: string[];
  }>;
  enable_metadata?: boolean;
}

export type IDocumentInfoFilter = {
  run_status: Record<number, number>;
  suffix: Record<string, number>;
  metadata: Record<string, Record<string, number>>;
};

export interface IKnowledgeFileParserConfig {
  chunk_token_num: number;
  layout_recognize: boolean;
  pages: number[][];
  task_page_size: number;
}

export interface IKnowledgeFile {
  source_version_id?: string;
  chunk_set_id?: string;
  chunk_num: number;
  create_date: string;
  create_time: number;
  created_by: string;
  id: string;
  kb_id: string;
  location: string;
  name: string;
  parser_id: string;
  process_begin_at?: any;
  process_duration: number;
  progress: number; // parsing process
  progress_msg: string; // parsing log
  run: RunningStatus; // parsing status
  size: number;
  source_type: string;
  status: string; // enabled
  thumbnail?: any; // base64
  token_num: number;
  type: string;
  update_date: string;
  update_time: number;
  parser_config: IKnowledgeFileParserConfig;
}

export enum RunningStatus {
  UNSTART = 'UNSTART', // need to run
  RUNNING = 'RUNNING', // need to cancel
  CANCEL = 'CANCEL', // need to refresh
  DONE = 'DONE', // need to refresh
  FAIL = 'FAIL', // need to refresh
  SCHEDULE = 'SCHEDULE',
}
