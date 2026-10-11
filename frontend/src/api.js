// Thin client for the CloudMorph backend. Same-origin: the Vite dev server (and the demo tunnel)
// proxy /deploy, /health, /fleet, /projects, /auth and /webhook to FastAPI, so no base URL and no CORS.
const TOKEN_KEY = 'cloudmorph.apiToken'

export const getToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const setToken = (t) => {
  const v = (t || '').trim() // pasted tokens often carry a trailing newline
  return v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY)
}

const headers = () => ({
  'Content-Type': 'application/json',
  ...(getToken() ? { 'X-API-Token': getToken() } : {}),
})

// --- ID/password accounts: a login returns a token that is used exactly like an API token ---------------
const AUTH_ERRORS = {
  401: '아이디 또는 비밀번호가 맞지 않습니다',
  403: '가입 코드가 맞지 않습니다',
  409: '이미 사용 중인 아이디입니다',
  429: '로그인을 너무 많이 실패했습니다. 몇 분 뒤에 다시 시도하세요',
}

async function authPost(path, body) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : '입력값을 확인하세요'
    throw new Error(AUTH_ERRORS[res.status] || (res.status === 422 ? detail : `요청 실패 (${res.status})`))
  }
  setToken(data.token)
  return data
}

export const signUp = ({ username, password, code }) => authPost('/auth/signup', { username, password, ...(code ? { code } : {}) })
export const logIn = ({ username, password }) => authPost('/auth/login', { username, password })

export async function logOut() {
  await fetch('/auth/logout', { method: 'POST', headers: headers() }).catch(() => {})
  setToken('')
}

/** { signup_code: bool }: whether sign-up asks for an invite code. */
export async function getAuthConfig() {
  const res = await fetch('/auth/config').catch(() => null)
  return res?.ok ? res.json() : { signup_code: false }
}

export async function startDeploy({ source, targets, name, ref }) {
  const body = { source, targets, ...(name ? { name } : {}), ...(ref ? { ref } : {}) }
  const res = await fetch('/deploy', { method: 'POST', headers: headers(), body: JSON.stringify(body) })
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 300)}`)
  return (await res.json()).deployment_id
}

export async function getDeployment(id) {
  const res = await fetch(`/deploy/${id}`, { headers: headers() })
  if (!res.ok) throw new Error(`${res.status}`)
  return res.json()
}

export async function getHealth() {
  try {
    const res = await fetch('/health')
    return res.ok
  } catch {
    return false
  }
}

export async function getFleet() {
  const res = await fetch('/fleet', { headers: headers() })
  if (!res.ok) return null // backend without the node pool
  return res.json()
}

/** Who the current token belongs to: { user, admin } or null (no/invalid token). */
export async function getMe() {
  const res = await fetch('/projects/me', { headers: headers() })
  return res.ok ? res.json() : null
}

/** Whether `token` maps to a user, checked before it replaces the stored one. */
export async function checkToken(token) {
  const res = await fetch('/projects/me', { headers: { 'X-API-Token': token } }).catch(() => null)
  if (!res) throw new Error('백엔드에 연결할 수 없습니다')
  return res.ok
}

async function postJson(path, body) {
  const res = await fetch(path, { method: 'POST', headers: headers(), body: JSON.stringify(body) })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || `${res.status}`)
  return data
}

/** Connect push-to-deploy. githubToken (optional) registers the webhook on GitHub; it is not stored. */
export const linkCd = (name, { githubToken, branch } = {}) =>
  postJson(`/projects/${encodeURIComponent(name)}/cd`, { github_token: githubToken || null, branch: branch || null })

export const unlinkCd = (name, { githubToken } = {}) =>
  postJson(`/projects/${encodeURIComponent(name)}/cd/unlink`, { github_token: githubToken || null })

/** The caller's on-premises agents: [{ name, online, busy, info }]. */
export async function getAgents() {
  const res = await fetch('/agent/status', { headers: headers() })
  return res.ok ? (await res.json()).agents : []
}

export async function getProjects() {
  const res = await fetch('/projects', { headers: headers() })
  if (!res.ok) return null
  return (await res.json()).projects
}

/** Subscribe to the SSE stream. `onEvent(type, payload, raw)`; returns a close() function. */
export function subscribe(id, onEvent, onFail) {
  const q = getToken() ? `?token=${encodeURIComponent(getToken())}` : ''
  const es = new EventSource(`/deploy/${id}/events${q}`)
  for (const type of ['stage', 'log', 'heal_diff', 'done', 'error']) {
    es.addEventListener(type, (ev) => {
      try {
        const raw = JSON.parse(ev.data)
        onEvent(type, raw.payload || {}, raw)
      } catch (e) {
        onEvent('log', { line: `[client] bad event: ${e}` }, null)
      }
    })
  }
  es.onerror = () => onFail && onFail()
  return () => es.close()
}
