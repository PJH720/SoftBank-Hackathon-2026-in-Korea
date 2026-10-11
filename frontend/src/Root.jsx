import { useEffect, useState } from 'react'
import App from './App.jsx'
import Shell from './Shell.jsx'

// The one-touch screens are the front door ("/", "#/deploy", "#/apps", "#/incidents").
// "#/3d" opens the team's 3D dashboard (App), full screen and unchanged.
const dashboard = () => window.location.hash.startsWith('#/3d')

export default function Root() {
  const [is3d, setIs3d] = useState(dashboard)
  useEffect(() => {
    const onHash = () => setIs3d(dashboard())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return is3d ? <App /> : <Shell />
}
