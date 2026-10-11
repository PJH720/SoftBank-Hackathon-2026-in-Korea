import { useEffect, useState } from 'react'
import App from './App.jsx'
import Shell from './Shell.jsx'
import AuthScreen from './components/AuthScreen.jsx'
import AdminPage from './admin/AdminPage.jsx'
import { getMe, getToken, setToken } from './api'

// "/" (and "#/3d") is the 3D dashboard (App), CloudMorph's main screen. The one-touch screens (Shell) live at
// "#/deploy", "#/apps" and "#/incidents", one link away from the dashboard header ("원터치") and back.
// "#/admin" is the admin console (every account and app), linked from the header for admin only.
const ONE_TOUCH = ['#/deploy', '#/apps', '#/incidents']
const screenFor = (hash) => (hash === '#/admin' ? 'admin' : ONE_TOUCH.includes(hash) ? 'onetouch' : 'dashboard')

/** The dashboard checks who the caller is on its own; the other screens get the same login gate here.
 * `children` may be a function of the signed-in user ({ user, admin }). */
function SignedIn({ children }) {
  const [token, setTokenState] = useState(getToken())
  const [me, setMe] = useState(undefined) // undefined until the first /projects/me answer, null = nobody
  useEffect(() => {
    let alive = true
    getMe().then((m) => alive && setMe(m)).catch(() => alive && setMe(null))
    return () => { alive = false }
  }, [token])
  if (me === undefined) return <div className="min-h-screen bg-[#0b0d17]" /> // don't flash a screen first
  if (!me) return <AuthScreen onAuthed={(t) => { setToken(t); setTokenState(t) }} />
  return typeof children === 'function' ? children(me) : children
}

export default function Root() {
  const [screen, setScreen] = useState(() => screenFor(window.location.hash))
  useEffect(() => {
    const onHash = () => setScreen(screenFor(window.location.hash))
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  if (screen === 'admin') return <SignedIn>{(me) => <AdminPage me={me} />}</SignedIn>
  return screen === 'onetouch' ? <SignedIn><Shell /></SignedIn> : <App />
}
