/** HTTP failures retain the machine-readable code used by conflict recovery. */
export class ReviewApiError extends Error {
  constructor(message, { code = "request_failed", status = null } = {}) {
    super(message);
    this.name = "ReviewApiError";
    this.code = code;
    this.status = status;
  }
}

export function isTransientRequest(error) {
  return error.name !== "AbortError" && (!error.status || error.status === 408 || error.status === 429 || error.status >= 500);
}

/** Authenticated transport and review endpoints; no DOM or mutable UI state. */
export function createApiClient({ fetch: fetcher = globalThis.fetch, getToken = async () => null,
  wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds)) } = {}) {
  async function request(url, options = {}) {
    const { responseType, ...fetchOptions } = options;
    const headers = new Headers(options.headers || {});
    const token = await getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    const response = await fetcher(url, { ...fetchOptions, headers });
    if (!response.ok) {
      const payload = response.headers.get("content-type")?.includes("json") ? await response.json() : await response.text();
      throw new ReviewApiError(payload?.error || `Request failed (${response.status})`, {
        code: payload?.code || "request_failed", status: response.status,
      });
    }
    if (responseType === "blob") return response.blob();
    if (responseType === "pdfPage") return {
      blob: await response.blob(), pageCount: Number(response.headers.get("X-PDF-Pages")) || null,
    };
    return response.headers.get("content-type")?.includes("json") ? response.json() : response.text();
  }

  async function requestWithRetry(url, options = {}, attempts = 2) {
    if (!Number.isInteger(attempts) || attempts < 1) throw new Error("Retry attempts must be a positive integer.");
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      try { return await request(url, options); }
      catch (error) {
        if (attempt === attempts || !isTransientRequest(error)) throw error;
        await wait(300 * attempt);
      }
    }
  }

  const paperPath = (endpoint, split, paperId) => `/api/${endpoint}/${encodeURIComponent(split)}/${encodeURIComponent(paperId)}`;
  const post = (endpoint, split, paperId, payload) => request(paperPath(endpoint, split, paperId), {
    method: "POST", body: JSON.stringify(payload),
  });
  return {
    request, requestWithRetry,
    schema: () => request("/api/study-schema"),
    paper: (split, paperId) => request(paperPath("paper", split, paperId)),
    mutate: (split, paperId, payload) => post("mutations", split, paperId, payload),
    reclassify: (split, paperId, payload) => post("record-reclassifications", split, paperId, payload),
    merge: (split, paperId, payload) => post("record-merges", split, paperId, payload),
    decide: (split, paperId, payload) => post("record-decisions", split, paperId, payload),
    download: url => request(url, { responseType: "blob" }),
  };
}
