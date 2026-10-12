# Raio-X IA — Definições do projeto

> Nome provisório. Ferramenta web em **português (PT-BR)**. O usuário submete a URL do seu site e recebe por e-mail um relatório sobre a "visibilidade para IA" desse site.
> A receita vem de propaganda no site, da newsletter (patrocínio e afiliados) e, no futuro, de um plano pago de monitoramento e consultoria.

## Visão em fases (roadmap)

| Fase | Produto | Custo por relatório | Status |
|---|---|---|---|
| **1** | **"Sua porta está aberta para a IA?"**: checker de acesso de crawlers de IA, llms.txt e legibilidade técnica | ~zero (sem LLM) | **ESCOPO ATUAL** |
| 2 | Raio-X GEO: perguntas reais ao ChatGPT, Gemini, Perplexity e Claude ("a IA recomenda sua empresa em [cidade]?") | APIs de LLM com busca web | futuro |
| 3 | "O que a IA diz sobre sua empresa": fact-check de marca com monitoramento semanal (recorrência para a newsletter) | APIs de LLM | futuro |
| 4 | Agent-Ready Score para e-commerce (WebMCP, schema Product, checkout agêntico) | baixo | futuro |

A arquitetura deve permitir adicionar novos **módulos de análise** (fases 2 a 4) sem reescrever o pipeline. Cada módulo é um "checker" plugável que recebe a URL e devolve resultados padronizados.

**Contexto de mercado (out/2026):**
- Desde 15/09/2026, a Cloudflare bloqueia por padrão crawlers de IA de treino e de agentes em domínios novos e no plano gratuito. Muitos donos de site estão invisíveis para a IA sem saber.
- Uma parte grande dos arquivos /llms.txt publicados é inválida, por exemplo HTML ou redirect para a home.

Esse é o gancho de marketing da Fase 1.

---

## Fase 1: escopo funcional

### Fluxo do usuário
1. A landing page (PT-BR) tem um campo de URL, um campo de e-mail e **dois consentimentos separados** (LGPD):
   - [obrigatório] aceitar receber o relatório e os termos/privacidade;
   - [opcional, desmarcado] assinar a newsletter.
2. Validação anti-abuso com Cloudflare Turnstile e rate limit.
3. Envio de um **e-mail de confirmação** (double opt-in). A análise só roda após o clique. Isso valida o lead e evita abuso.
4. O job entra na fila, o worker executa os checks e o resultado é salvo.
5. Um e-mail com o resumo (score e top 3 problemas) é enviado com link para o **relatório web** em `/r/{token}`. O token deve ser não adivinhável.
6. A página do relatório tem espaços para anúncios (placeholders), um CTA para a newsletter e um CTA de "quero ajuda / consultoria".

### Checks da Fase 1
Cada check retorna: `id`, `status` (ok | aviso | erro | indeterminado), `titulo`, `explicacao` (PT-BR, linguagem leiga), `como_corrigir`, `evidencia` (dados brutos) e `peso`.

1. **robots.txt × crawlers de IA**: fazer o parse e informar, por user-agent, se está permitido ou bloqueado. Lista de tokens atuais:
   - **OpenAI:** GPTBot, OAI-SearchBot, ChatGPT-User
   - **Anthropic:** ClaudeBot, Claude-SearchBot, Claude-User
   - **Perplexity:** PerplexityBot, Perplexity-User
   - **Google:** Google-Extended, Googlebot
   - **Outros:** Bingbot, Applebot-Extended, CCBot, Meta-ExternalAgent, Bytespider
   - Separar as categorias **treino × busca/usuário**: bloquear treino é escolha legítima, mas bloquear busca tira o site das respostas.
   - Detectar tokens aposentados (`anthropic-ai`, `Claude-Web`) e avisar que regras com eles não fazem nada.
   - Manter essa lista em um arquivo de config versionado (`config/ai_agents.yaml`), porque ela muda com frequência.
2. **Bloqueio na borda (CDN/WAF)**: buscar a home com UA de navegador e com o UA de cada bot, e comparar status, tamanho e sinais de challenge (403/503, `cf-mitigated`, HTML de challenge).
   - Detectar o CDN pelos headers (`server`, `cf-ray` etc.).
   - **Ressalva obrigatória no relatório:** nossas requisições não vêm dos IPs verificados dos bots, então o resultado é *indicativo*. Quando for Cloudflare, mostrar o passo a passo para conferir no painel.
3. **llms.txt / llms-full.txt**: verificar se existe, retorna 200 sem redirect para a home, usa content-type de texto, **não é HTML** e segue a estrutura markdown esperada (H1, blockquote de resumo, seções com links). Checar amostralmente se os links funcionam.
4. **Meta robots / X-Robots-Tag**: verificar `noindex`, `nofollow`, `noai` e `noimageai` na home.
5. **Dependência de JavaScript**: comparar o texto do HTML bruto com o HTML renderizado (Playwright). A maioria dos crawlers de IA não executa JS. Se o conteúdo principal só aparece após a renderização, gerar um alerta forte.
6. **Dados estruturados**: verificar a presença de JSON-LD (Organization, LocalBusiness, Product, FAQPage) e se o JSON é válido.
7. **Sitemap**: verificar se ele existe (via robots.txt ou `/sitemap.xml`) e se é acessível.
8. **Básicos**: HTTPS, tempo de resposta da home, `<title>`, meta description e idioma declarado.

**Score de 0 a 100** pela soma ponderada dos checks. A classificação em faixas é: "Porta aberta", "Entreaberta" e "Fechada para a IA". Os pesos ficam em config.

### Fora do escopo da Fase 1
Chamadas a LLMs, contas de usuário e login, pagamento, monitoramento recorrente e crawling de múltiplas páginas (só analisar a home e os arquivos raiz).

---

## Stack técnica (decisões iniciais)

- **Backend:** Python 3.12 + FastAPI
- **Fila:** Redis + RQ (simples). Jobs com timeout (60s) e retry limitado.
- **Banco:** PostgreSQL. Tabelas: `leads`, `consents` (com timestamp, IP e versão do texto aceito), `analyses`, `check_results`.
- **Fetch/render:** httpx (HTTP) + Playwright Chromium (renderização), num worker separado.
- **E-mail:** abstração com provider plugável (Resend ou Amazon SES) e templates Jinja2 em HTML responsivo.
- **Front:** HTML server-side (Jinja2) + Tailwind, sem SPA. Precisa ser leve e bom para SEO. O visual final virá do Claude Design, então manter os templates fáceis de trocar.
- **Infra:** Docker Compose (api, worker, redis, postgres, caddy). Caddy como reverse proxy com TLS automático. Deploy numa VPS Linux própria.
- **Config:** variáveis em `.env` (nunca commitar segredos) e um `.env.example` documentado.

## Requisitos não funcionais (importantes)

- **Proteção contra SSRF (crítico):** a URL vem do usuário. É preciso:
  - resolver o DNS e bloquear IPs privados, loopback, link-local e metadata (169.254.169.254), além de IPv6 equivalentes;
  - revalidar o IP a cada redirect;
  - limitar a 5 redirects;
  - aceitar só http/https e portas 80/443;
  - limitar o tamanho da resposta (5 MB) e aplicar timeouts.
- **Rate limit:** por IP, por e-mail e por domínio analisado. Se o mesmo domínio foi analisado nas últimas 24h, reutilizar o resultado.
- **LGPD:** página de privacidade, consentimentos separados e registrados, link de descadastro em todo e-mail e endpoint para excluir dados.
- **Identificação do nosso fetcher:** usar um UA próprio identificável e uma página `/bot` explicando o serviço. Os testes com UA de bots servem só para a comparação do check 2.
- **Observabilidade:** logs estruturados (JSON) e métricas simples (análises por dia, taxa de confirmação de e-mail, taxa de opt-in na newsletter).
- **Testes:** pytest com fixtures de sites simulados (robots.txt variados, llms.txt inválido, challenge da Cloudflare, SPA só com JS). Não depender de sites reais nos testes.

## Marcos da Fase 1

1. **M1, núcleo de análise:** módulo `checkers/` com os 8 checks, uma CLI `python -m raioxia analyze <url>` que imprime JSON, e testes.
2. **M2, pipeline:** API, fila, worker, banco, double opt-in e e-mails.
3. **M3, front:** landing, página de confirmação, relatório web, privacidade/termos e placeholders de anúncio.
4. **M4, deploy:** Docker Compose, Caddy e um guia `DEPLOY.md` para a VPS.

Critério de pronto da Fase 1: um usuário real submete a URL, confirma o e-mail e recebe o relatório em menos de 2 minutos. Para isso, a proteção SSRF e o rate limit precisam estar ativos e testados.

## Convenções

- Todo texto voltado ao usuário em PT-BR. Código, nomes de variáveis e commits em inglês.
- As explicações dos checks devem ser para leigos (dono de pequeno negócio), sem jargão, cada uma com um "como corrigir" acionável.
- Commits pequenos, um marco por vez. Perguntar antes de decisões de arquitetura que fujam deste documento.
