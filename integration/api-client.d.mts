import type {CatalogueOptions, HealthResponse, InputIssue, RecommendationRequest, RecommendationResponse} from './api-types.js';

export type ClientErrorKind = 'http' | 'network' | 'timeout' | 'cancelled' | 'invalid_response' | 'input';
export class ApiClientError extends Error {
  kind: ClientErrorKind;
  status?: number;
  code?: string;
  details: InputIssue[];
  requestId?: string;
  constructor(kind: ClientErrorKind, message: string, options?: {status?: number; code?: string; details?: InputIssue[]; requestId?: string});
}
export interface CallOptions { signal?: AbortSignal }
export interface ApiClient {
  health(options?: CallOptions): Promise<HealthResponse>;
  catalogueOptions(options?: CallOptions): Promise<CatalogueOptions>;
  recommend(request: RecommendationRequest, options?: CallOptions): Promise<RecommendationResponse>;
}
export function createApiClient(options?: {baseUrl?: string; timeoutMs?: number; fetchImpl?: typeof fetch}): ApiClient;
export function buildRecommendationRequest(form: Record<string, unknown>): RecommendationRequest;
export function createLatestRecommender(client: ApiClient): {
  recommend(request: RecommendationRequest): Promise<RecommendationResponse | null>;
  cancel(): void;
};
