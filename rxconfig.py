import os

import reflex as rx

config = rx.Config(
    app_name="bike_tracker",
    db_url=os.environ.get("REFLEX_DB_URL", "sqlite:///reflex.db"),
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
