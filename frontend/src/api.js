// Thin client for the CloudMorph backend. Same-origin: the Vite dev server (and the demo tunnel)
// proxy /deploy, /health, /fleet, /projects and /webhook to FastAPI, so no base URL and no CORS.
const TOKEN_KEY = 'cloudmorph.apiToken'

export const getToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const setToken = (t) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY))

const headers = () => ({
  'Content-Type': 'application/json',
  ...(getToken() ? { 'X-API-Token': getToken() } : {}),
})

async function post(path, body) {
  const res = await fetch(path, { method: 'POST', headers: headers(), body: JSON.stringify(body || {}) })
  if (!res.ok) {
    const text = await res.text()
    let detail = text
    try { detail = JSON.parse(text).detail || text } catch { /* not JSON */ }
    throw new Error(typeof detail === 'string' ? detail.slice(0, 300) : `${res.status}`)
  }
  return res.json()
}

/** What the incident guard sees and did: { guard, health, incidents }. */
export async function getIncidents() {
  const res = await fetch('/incidents', { headers: headers() })
  if (!res.ok) return null
  return res.json()
}

/** Disaster drill: kind = crash | db_down | outage. */
export const drill = (name, kind) => post(`/projects/${encodeURIComponent(name)}/drill`, { kind })
/** Drag & drop: { kind: postgres|mysql|redis, mode: new|url|none, url? } -> { deployment_id, warning } */
export const setResource = (name, body) => post(`/projects/${encodeURIComponent(name)}/resources`, body)
/** Run the app somewhere else, then take the old place down. */
export const moveProject = (name, to) => post(`/projects/${encodeURIComponent(name)}/move`, { to })
/** Answer input_required with values and, when asked, where to deploy. */
export const submitAnswers = (id, values, targets) => post(`/deploy/${id}/env`, targets ? { values, targets } : { values })

export async function startDeploy({ source, targets, name, ref }) {
  // ask_env: pause after analysis and ask for the values the code needs (EnvRequestCard answers)
  const body = { source, targets, ask_env: true, ...(name ? { name } : {}), ...(ref ? { ref } : {}) }
  const res = await fetch('/deploy', { method: 'POST', headers: headers(), body: JSON.stringify(body) })
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 300)}`)
  return (await res.json()).deployment_id
}

/** Answer the healer's question (asked before any PR): 'continue' (PR + deploy), 'pr_only' or 'stop'. */
export async function answerQuestion(id, questionId, choice) {
  const res = await fetch(`/deploy/${id}/answer`, { method: 'POST', headers: headers(), body: JSON.stringify({ question_id: questionId, choice }) })
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 300)}`)
}

/** Answer an `input_required` event. Values only travel in this request; the backend never echoes them. */
export async function submitEnv(id, values) {
  const res = await fetch(`/deploy/${id}/env`, { method: 'POST', headers: headers(), body: JSON.stringify({ values }) })
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 300)}`)
  return res.json()
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

export async function getProjects() {
  const res = await fetch('/projects', { headers: headers() })
  if (!res.ok) return null
  return (await res.json()).projects
}

/** Stop a running deployment at its next step (an in-flight docker build is killed right away). */
export async function cancelDeploy(id) {
  const res = await fetch(`/deploy/${id}/cancel`, { method: 'POST', headers: headers() })
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 300)}`)
  return res.json()
}

/** Take down everything the app is serving (local container + tunnel, Cloud Run service, node replicas). */
export async function stopProject(name) {
  const res = await fetch(`/projects/${encodeURIComponent(name)}/stop`, { method: 'POST', headers: headers() })
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 300)}`)
  return res.json()
}

/** Subscribe to the SSE stream. `onEvent(type, payload, raw)`; returns a close() function. */
export function subscribe(id, onEvent, onFail) {
  const q = getToken() ? `?token=${encodeURIComponent(getToken())}` : ''
  const es = new EventSource(`/deploy/${id}/events${q}`)
  for (const type of ['stage', 'log', 'heal_diff', 'input_required', 'done', 'error']) {
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
