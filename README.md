# Adotaê

## Sobre o projeto

O Adotaê é um projeto acadêmico de Engenharia de Software voltado à divulgação de animais domésticos disponíveis para adoção em **São João da Barra (RJ)**. O site facilita o contato entre pessoas interessadas e responsáveis pelos anúncios, com uma interface simples e responsiva para computadores e celulares.

A aplicação está publicada na **Vercel**, conectada ao projeto principal do **Supabase**, com os fluxos principais validados manualmente.

**Aplicação publicada:** [Acessar o Adotaê](https://projeto-adotae.vercel.app/).

## Funcionalidades

- **Catálogo público:** consulta de animais disponíveis, com filtros combináveis por tipo de animal, porte e distrito.
- **Detalhes dos animais:** fotografia, descrição, localização e contatos de e-mail e/ou WhatsApp escolhidos pelo anunciante.
- **Cadastro e autenticação:** criação de conta, confirmação de e-mail, login e logout pelo Supabase Auth. A confirmação do e-mail não inicia o login automaticamente.
- **Meus anúncios:** listagem dos próprios anúncios, com filtros por publicados, rascunhos e adotados. As ações de gerenciamento exigem autenticação e respeitam a titularidade do anúncio.
- **Criação e edição:** cadastro de animais como rascunho e edição dos próprios rascunhos. Contato e fotografia são opcionais ao salvar um rascunho.
- **Fotografias:** envio de uma fotografia por anúncio, nos formatos JPEG, PNG ou WebP, com até 3 MiB. As imagens são validadas, convertidas para WebP e exibidas no catálogo, nos detalhes e em miniaturas na conta.
- **Publicação:** publicação direta no cadastro ou posterior de um rascunho, exigindo e-mail confirmado, pelo menos um contato válido e uma fotografia pronta.
- **Registro de adoção:** o responsável pode marcar um anúncio publicado como adotado, após confirmação. O anúncio sai do catálogo público e permanece na conta com o status de adotado.
- **Remoção lógica:** rascunhos e anúncios publicados disponíveis podem ser excluídos após confirmação. O anúncio deixa de aparecer na plataforma, mas os registros e as fotografias são preservados no armazenamento. **Anúncios adotados não podem ser removidos.**
- **Feedback:** o link “Avaliar o site”, disponível na navegação, abre um formulário externo para avaliação do projeto.

O contato sobre a adoção ocorre diretamente pelos canais divulgados no anúncio. Não há formulário de solicitação de adoção nem chat interno.

## Tecnologias utilizadas

- **Backend:** Python e Flask.
- **Frontend:** HTML, CSS, JavaScript e templates Jinja2.
- **Banco de dados:** PostgreSQL no Supabase, com políticas de segurança por linha (RLS).
- **Autenticação e arquivos:** Supabase Auth e Supabase Storage, integrados pelo SDK oficial para Python.
- **Formulários e configuração:** Flask-WTF, validação no servidor, proteção CSRF e python-dotenv.
- **Processamento de imagens:** Pillow.
- **Hospedagem:** Vercel.
- **Testes automatizados:** unittest, mocks e transporte HTTP simulado.

As dependências estão listadas em [requirements.txt](requirements.txt).

## Como executar localmente

Com Python 3.10 ou superior instalado, abra o terminal na pasta do repositório.

### 1. Preparar o ambiente

No Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

No Linux ou macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Se o ambiente `.venv` ou o arquivo `.env` já existir, preserve-o e pule o respectivo comando de criação ou cópia.

### 2. Configurar as variáveis

Preencha o `.env` seguindo o [.env.example](.env.example):

- `SUPABASE_URL`: URL do projeto Supabase utilizado.
- `SUPABASE_PUBLISHABLE_KEY`: chave publicável do mesmo projeto. Não utilize chave administrativa.
- `SECRET_KEY`: segredo aleatório para a sessão do Flask e a proteção CSRF.
- `ADOTAE_LOCAL_HTTP=true`: permite o uso de cookies em HTTP no ambiente local. Em produção, omita a variável ou use `false` e utilize HTTPS.

Para gerar o valor de `SECRET_KEY`, execute `python -c "import secrets; print(secrets.token_hex(32))"` (use `python3` no Linux ou macOS). Não publique o `.env` nem compartilhe seu conteúdo.

O projeto Supabase deve ter o banco, as políticas de acesso e o Storage configurados conforme [supabase/README.md](supabase/README.md). As migrações não são executadas automaticamente ao iniciar a aplicação.

Para validar a confirmação de e-mail em um Supabase próprio, habilite o cadastro por e-mail, mantenha **Confirm email** ativado e configure o envio de mensagens. O template **Confirm sign up** deve apontar para a rota da aplicação:

```html
<a href="{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&amp;type=email">Confirmar meu e-mail</a>
```

Em um ambiente Supabase separado para desenvolvimento, use `http://localhost:5000` como **Site URL**. No projeto principal, preserve a URL da aplicação publicada.

### 3. Iniciar a aplicação

No Windows (PowerShell):

```powershell
.\.venv\Scripts\python.exe run.py
```

No Linux ou macOS:

```bash
.venv/bin/python run.py
```

Abra **http://localhost:5000** no navegador. Para encerrar, pressione `Ctrl+C` no terminal.

## Testes e validação

### Testes automatizados

A suíte Python cobre catálogo e filtros, detalhes, cadastro, confirmação de e-mail, login e sessão, gerenciamento de anúncios, publicação, adoção, remoção lógica e processamento de fotografias. Também verifica validações, proteção CSRF, restrições de acesso e tratamento de falhas.

Para executar no Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

No Linux ou macOS, use `.venv/bin/python -m unittest discover -v`.

Os testes em `tests/` utilizam mocks, HTTP simulado e imagens geradas em memória, sem depender de credenciais reais ou de chamadas ao Supabase. Eles verificam o comportamento da aplicação, mas não comprovam as políticas instaladas no serviço remoto. Os roteiros de validação do banco e do Storage têm instruções e resultados separados em [supabase/README.md](supabase/README.md).

### Validações manuais

Os fluxos principais foram validados manualmente na aplicação publicada na Vercel, conectada ao Supabase principal. As validações incluem catálogo, filtros, detalhes, fotografias e gerenciamento de anúncios, além de conferências da interface em computador e celular.

Essas verificações complementam os testes automatizados; não representam uma avaliação exaustiva de segurança, desempenho ou compatibilidade.

## Versão 1.0 — Escopo da entrega

O Adotaê foi desenvolvido como projeto acadêmico de Engenharia de Software, com foco na divulgação de animais disponíveis para adoção em São João da Barra (RJ).

Esta primeira versão encontra-se concluída e publicada, contemplando as funcionalidades propostas para o escopo da entrega: consulta e filtragem de animais, visualização de detalhes, autenticação de usuários, criação e gerenciamento de anúncios, publicação de fotografias e registro de adoções.

A comunicação entre interessados e anunciantes ocorre diretamente pelos contatos disponibilizados nos anúncios, sem intermediação da plataforma.

A versão foi desenvolvida utilizando serviços gratuitos e possui regras específicas para gerenciamento dos anúncios, incluindo edição de rascunhos, remoção lógica e preservação dos registros de animais adotados.

Funcionalidades como chat interno, gerenciamento de organizações e intermediação formal do processo de adoção não integram o escopo desta versão.

## Documentação técnica complementar

Consulte [supabase/README.md](supabase/README.md) para detalhes de migrações, permissões, políticas RLS, armazenamento de fotografias e roteiros de validação do banco e do Storage.
