export type ChunkDocType = 'image' | 'table' | 'text';

export interface IChunk {
  source_version_id?: string;
  available_int: number; // Whether to enable, 0: not enabled, 1: enabled
  chunk_id: string;
  content_with_weight: string;
  doc_id: string;
  doc_name: string;
  doc_type_kwd?: ChunkDocType;
  document_relative_path?: string; // Library-relative, never a server filesystem path.
  document_url?: string;
  image_id: string;
  important_kwd?: string[];
  question_kwd?: string[]; // keywords
  tag_kwd?: string[];
  positions: number[][];
  tag_feas?: Record<string, number>;
  parse_run_id?: string;
  chunk_set_id?: string;
  parser_platform?: Record<string, any>;
  provenance?: Array<Record<string, any>>;
  office_locator?: {
    kind: 'docx' | 'xlsx' | 'pptx';
    heading_path?: string[];
    item_locator?: string;
    sheet?: string;
    cell_range?: string;
    region_locator?: string;
    slide?: number;
    shape_locator?: string;
    bbox?: number[];
  };
  hwp_locator?: {
    kind: 'hwp' | 'hwpx';
    section_index: number;
    paragraph_index?: number;
    block_locator: string;
    table?: {
      row: number;
      column: number;
      rowspan: number;
      colspan: number;
    };
  };
  display_html?: string;
}

export interface ITestingChunk {
  source_version_id?: string;
  chunk_id: string;
  content_ltks: string;
  content_with_weight: string;
  doc_id: string;
  doc_name: string;
  img_id: string;
  image_id: string;
  important_kwd: any[];
  kb_id: string;
  similarity: number;
  term_similarity: number;
  vector: number[];
  vector_similarity: number;
  highlight: string;
  positions: number[][];
  docnm_kwd: string;
  doc_type_kwd: string;
  document_relative_path?: string;
  document_metadata?: Record<string, any>;
  parse_run_id?: string;
  chunk_set_id?: string;
  parser_platform?: Record<string, any>;
  provenance?: Array<Record<string, any>>;
  office_locator?: IChunk['office_locator'];
  hwp_locator?: IChunk['hwp_locator'];
}
