"""Presets de canal: pré-preenchem template de roteiro e estilo visual (editáveis depois)."""

CHANNEL_PRESETS: list[dict[str, object]] = [
    {
        "key": "misterio",
        "label": "Mistério",
        "fill": {
            "script_template": (
                "Narrador grave e calmo, tom de documentário sombrio. Frases curtas. "
                "Abra com um gancho forte nos primeiros 10 segundos, construa tensão por camadas "
                "e feche com uma pergunta que fique na cabeça do espectador. Sem sensacionalismo "
                "nem afirmações sem fonte."
            ),
            "visual_style_prompt": (
                "dark cinematic still, low-key lighting, heavy shadows, fog, 35mm film grain, "
                "muted teal and amber palette, no text, no watermark"
            ),
            "target_duration_minutes": "8",
        },
    },
    {
        "key": "historia",
        "label": "História sombria",
        "fill": {
            "script_template": (
                "Narrador sério e envolvente, como um professor que conta um segredo. "
                "Contextualize época e lugar nas primeiras cenas, siga ordem cronológica, "
                "destaque datas e nomes com clareza e termine com a consequência histórica."
            ),
            "visual_style_prompt": (
                "dark historical painting style, chiaroscuro, aged parchment textures, "
                "desaturated earth tones, dramatic candlelight, no text"
            ),
            "target_duration_minutes": "10",
        },
    },
    {
        "key": "curiosidades",
        "label": "Curiosidades",
        "fill": {
            "script_template": (
                "Narrador curioso e ágil. Lista numerada de fatos, cada um com gancho próprio, "
                "dado surpreendente e uma frase de transição. Ritmo rápido, linguagem simples."
            ),
            "visual_style_prompt": (
                "moody conceptual illustration, dark background, neon rim light, clean composition, "
                "high contrast, no text"
            ),
            "target_duration_minutes": "6",
        },
    },
    {
        "key": "truecrime",
        "label": "True crime",
        "fill": {
            "script_template": (
                "Narrador contido e respeitoso. Apresente o caso em ordem cronológica, "
                "separe fatos comprovados de hipóteses, evite detalhes gráficos e não exponha "
                "pessoas reais sem necessidade. Feche com o estado atual do caso."
            ),
            "visual_style_prompt": (
                "noir crime-scene atmosphere, rain-soaked streets at night, sodium vapor light, "
                "grainy film, desaturated, no faces in close-up, no text"
            ),
            "target_duration_minutes": "12",
        },
    },
]
