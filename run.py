"""Aplicação demonstrativa do projeto acadêmico Adotaê."""

from flask import Flask, abort, render_template, request

app = Flask(
    __name__,
    template_folder="app/templates",
    static_folder="app/static",
)

# Dados temporários para apresentar a interface. Não representam animais reais.
ANIMAIS = [
    {
        "id": 1,
        "nome": "Bento",
        "distrito": "São João da Barra (sede)",
        "especie": "Cachorro",
        "sexo": "Macho",
        "idade": "2 anos",
        "porte": "Médio",
        "descricao": "Companheiro de caminhadas e de tardes preguiçosas no sofá.",
        "imagem": "bento.svg",
        "imagem_alt": "Ilustração de Bento, um cachorro caramelo de orelhas caídas.",
    },
    {
        "id": 2,
        "nome": "Luna",
        "distrito": "Atafona",
        "especie": "Gato",
        "sexo": "Fêmea",
        "idade": "1 ano",
        "porte": "Pequeno",
        "descricao": "Curiosa, tranquila e especialista em encontrar um cantinho de sol.",
        "imagem": "luna.svg",
        "imagem_alt": "Ilustração de Luna, uma gata cinza de olhos verdes.",
    },
    {
        "id": 3,
        "nome": "Paçoca",
        "distrito": "Grussaí",
        "especie": "Cachorro",
        "sexo": "Macho",
        "idade": "6 meses",
        "porte": "Pequeno",
        "descricao": "Um filhote cheio de energia, sempre pronto para uma brincadeira.",
        "imagem": "pacoca.svg",
        "imagem_alt": "Ilustração de Paçoca, um cachorro claro com uma mancha marrom.",
    },
    {
        "id": 4,
        "nome": "Amora",
        "distrito": "Barcelos",
        "especie": "Gato",
        "sexo": "Fêmea",
        "idade": "3 anos",
        "porte": "Pequeno",
        "descricao": "Carinhosa e observadora. Gosta de companhia e de uma boa soneca.",
        "imagem": "amora.svg",
        "imagem_alt": "Ilustração de Amora, uma gata preta de olhos amarelos.",
    },
]

OPCOES_FILTROS = {
    "tipo": ("Cachorro", "Gato", "Ave", "Pequeno mamífero", "Outro animal doméstico"),
    "porte": ("Pequeno", "Médio", "Grande"),
    "distrito": ("São João da Barra (sede)", "Atafona", "Grussaí", "Cajueiro", "Pipeiras", "Barcelos"),
}


@app.get("/")
def inicio():
    return render_template("index.html")


@app.get("/animais")
def animais():
    filtros = {}
    for campo, opcoes in OPCOES_FILTROS.items():
        valor = request.args.get(campo, "")
        filtros[campo] = valor if valor in opcoes else ""

    animais_filtrados = [
        animal for animal in ANIMAIS
        if (not filtros["tipo"] or animal["especie"] == filtros["tipo"])
        and (not filtros["porte"] or animal["porte"] == filtros["porte"])
        and (not filtros["distrito"] or animal["distrito"] == filtros["distrito"])
    ]
    return render_template(
        "animais.html",
        animais=animais_filtrados,
        filtros=filtros,
        opcoes_filtros=OPCOES_FILTROS,
    )


@app.get("/animais/<int:id>")
def animal_detalhes(id):
    for animal in ANIMAIS:
        if animal["id"] == id:
            return render_template("animal_detalhes.html", animal=animal)
    abort(404)


if __name__ == "__main__":
    app.run(debug=True)
