/** Normalize the optional dashboard override using the deployment API contract. */
export function normalizeLlmConfig(selection) {
  if (!selection?.provider || selection.provider === 'server') return undefined
  const { provider } = selection
  if (!['anthropic', 'openai', 'local'].includes(provider)) {
    throw new Error('자가치유 AI 제공자를 선택해 주세요.')
  }
  const model = selection.model?.trim()
  const baseUrl = selection.base_url?.trim()
  if (provider !== 'local') {
    if (baseUrl) throw new Error('Claude와 OpenAI에는 로컬 서버 URL을 지정할 수 없어요.')
    return { provider, ...(model ? { model } : {}) }
  }
  if (!model) throw new Error('로컬 AI의 모델명을 입력해 주세요.')
  let url
  try { url = new URL(baseUrl) } catch { /* Report a single actionable URL error below. */ }
  if (!url || !['http:', 'https:'].includes(url.protocol)) {
    throw new Error('로컬 AI의 HTTP 또는 HTTPS 서버 URL을 입력해 주세요.')
  }
  return { provider, model, base_url: baseUrl }
}
