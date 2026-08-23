import os

import reflex as rx

config = rx.Config(
    app_name="bike_tracker",
    db_url=os.environ.get("REFLEX_DB_URL", "sqlite:///reflex.db"),
    # Reflex reads no dotenv file by default. Without naming one here, a
    # `.env` next to this file is simply ignored and the Hammerhead
    # settings card keeps saying "not configured" with nothing to explain
    # why. Loaded from `Config.__init__`, so it lands before the app module
    # imports and long before `ClientCredentials.from_env()` runs.
    #
    # Note it loads with `override=True`: a value in `.env` beats one
    # already exported in the shell, not the other way round.
    env_file=".env",
    default_color_mode="system",
    plugins=[
        # Declare the theme here, not via rx.App(theme=...) — that argument is
        # deprecated in 0.9.x. Declaring the plugin explicitly also suppresses
        # the "implicit Radix Themes enablement" deprecation warning.
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(
                accent_color="blue",
                gray_color="slate",
                radius="medium",
                scaling="100%",
            ),
        ),
        rx.plugins.TailwindV4Plugin(),
        rx.plugins.SitemapPlugin(),
    ],
)
