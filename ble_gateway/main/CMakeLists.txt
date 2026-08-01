idf_component_register(
    SRCS
        "main.c"
        "gateway_profile.c"
        "gateway_transport.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES bt driver nvs_flash
)
