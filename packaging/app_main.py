"""py2app entry point for the VoiceFlow.app bundle.

The `voiceflow` package is installed in the project venv, so this imports
regardless of working directory.
"""

from voiceflow.main import main

if __name__ == "__main__":
    main()
