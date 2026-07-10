import { useEffect, useState } from 'react'

import { BackendSetupWizard } from './BackendSetupWizard'

export function ElectronShell() {
  const [isFirstLaunch, setIsFirstLaunch] = useState(false)

  useEffect(() => {
    window.electronAPI!.isFirstLaunch().then(setIsFirstLaunch)
  }, [])

  return <BackendSetupWizard open={isFirstLaunch} />
}
