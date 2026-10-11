import { useEffect, useState } from 'react'
import App from './App.jsx'
import Shell from './Shell.jsx'
import AuthScreen from './components/AuthScreen.jsx'
import { getMe, getToken, setToken } from './api'

// "/" (and "#/3d") is the 3D dashboard (App), CloudMorph's main screen. The one-touch screens (Shell) live at
// "#/deploy", "#/apps" and "#/incidents", one link away from the dashboard header ("원터치") and back.
const ONE_TOUCH = ['#/deploy', '#/apps', '#/incidents']
const oneTouch = () => ONE_TOUCH.includes(window.location.hash)

/** The dashboard checks who the caller is on its own; the one-touch screens get the same login gate here. */
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
  return children
}

export default function Root() {
  const [isOneTouch, setIsOneTouch] = useState(oneTouch)
  useEffect(() => {
    const onHash = () => setIsOneTouch(oneTouch())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return isOneTouch ? <SignedIn><Shell /></SignedIn> : <App />
}
