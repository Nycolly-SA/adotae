"""Aplicação demonstrativa do projeto acadêmico Adotaê."""

from flask import Flask, render_template

app = Flask(
    __name__,
    template_folder="app/templates",
    static_folder="app/static",
)

# Dados temporários para apresentar a interface. Não representam animais reais.
ANIMAIS = [
    {
        "nome": "Bento",
        "especie": "Cachorro",
        "sexo": "Macho",
        "idade": "2 anos",
        "porte": "Médio",
        "descricao": "Companheiro de caminhadas e de tardes preguiçosas no sofá.",
        "imagem": "bento.svg",
        "imagem_alt": "Ilustração de Bento, um cachorro caramelo de orelhas caídas.",
    },
    {
        "nome": "Luna",
        "especie": "Gata",
        "sexo": "Fêmea",
        "idade": "1 ano",
        "porte": "Pequeno",
        "descricao": "Curiosa, tranquila e especialista em encontrar um cantinho de sol.",
        "imagem": "luna.svg",
        "imagem_alt": "Ilustração de Luna, uma gata cinza de olhos verdes.",
    },
    {
        "nome": "Paçoca",
        "especie": "Cachorro",
        "sexo": "Macho",
        "idade": "6 meses",
        "porte": "Pequeno",
        "descricao": "Um filhote cheio de energia, sempre pronto para uma brincadeira.",
        "imagem": "pacoca.svg",
        "imagem_alt": "Ilustração de Paçoca, um cachorro claro com uma mancha marrom.",
    },
    {
        "nome": "Amora",
        "especie": "Gata",
        "sexo": "Fêmea",
        "idade": "3 anos",
        "porte": "Pequeno",
        "descricao": "Carinhosa e observadora. Gosta de companhia e de uma boa soneca.",
        "imagem": "amora.svg",
        "imagem_alt": "Ilustração de Amora, uma gata preta de olhos amarelos.",
    },
]


@app.get("/")
def inicio():
    return render_template("index.html")


@app.get("/animais")
def animais():
    return render_template("animais.html", animais=ANIMAIS)


if __name__ == "__main__":
    app.run(debug=True)
