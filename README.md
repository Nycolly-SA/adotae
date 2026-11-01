# Adotaê

Projeto acadêmico de Engenharia de Software com o objetivo de facilitar a adoção responsável de animais.

## Etapa atual

Esta primeira versão apresenta o projeto e um catálogo com quatro animais fictícios. O site se adapta a celulares e computadores. O catálogo é demonstrativo: ainda não é possível cadastrar animais ou solicitar uma adoção.

Em `/animais`, é possível combinar filtros por tipo de animal, porte e distrito. Use “Aplicar filtros” para consultar e “Limpar” para voltar ao catálogo completo. Os filtros são enviados por parâmetros GET na URL.

## Tecnologias

- Python e Flask
- HTML, CSS e JavaScript

## Como executar

Com o Python 3.10 ou superior instalado, abra o terminal na pasta do projeto.

No Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

No Linux ou macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

A criação do ambiente e a instalação das dependências são necessárias apenas na primeira vez. Se o ambiente `.venv` já existir, pule o primeiro comando.

Depois, abra **http://localhost:5000** no navegador. Para encerrar, pressione `Ctrl+C` no terminal.

## Testes

Os testes verificam as páginas, os links, os arquivos do site e os filtros do catálogo (incluindo combinações, resultado vazio, limpeza e parâmetros desconhecidos). Para executá-los no Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

No Linux ou macOS, use `.venv/bin/python -m unittest discover -v`.
