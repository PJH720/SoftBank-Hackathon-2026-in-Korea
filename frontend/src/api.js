// Thin client for the CloudMorph backend. Same-origin: the Vite dev server (and the demo tunnel)
// proxy /deploy, /health, /fleet, /projects and /webhook to FastAPI, so no base URL and no CORS.
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
