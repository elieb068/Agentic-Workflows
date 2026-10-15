"""Landing page copy kept as data (PT-BR), so the FAQ can also feed the FAQPage JSON-LD."""

STEPS = [
    {"title": "Informe o endereço", "text": "Só a página inicial e os arquivos públicos da raiz, como o robots.txt. Não mexemos em nada."},
    {"title": "Confirme seu e-mail", "text": "Um clique no link que enviamos. Isso evita abuso e garante que o relatório chegue a você."},
    {"title": "Receba o relatório", "text": "Nota de 0 a 100, os 3 maiores problemas no e-mail e o relatório completo com o passo a passo para corrigir."},
]

# One line per check, in the order of the 8 checks (ids match raioxia/checkers).
CHECKS = [
    {"id": "robots_ai", "title": "Robôs de IA no robots.txt", "text": "Se o seu robots.txt deixa os robôs do ChatGPT, Claude, Gemini e Perplexity lerem o site."},
    {"id": "edge_block", "title": "Bloqueio no firewall ou CDN", "text": "Se a Cloudflare ou outro firewall barra os robôs de IA antes de eles chegarem ao site."},
    {"id": "llms_txt", "title": "Arquivo llms.txt", "text": "Se existe o arquivo que apresenta seu site aos assistentes de IA, e se ele é válido."},
    {"id": "meta_robots", "title": "Instruções de indexação", "text": "Se a página pede para não ser indexada ou usada por IA (noindex, noai)."},
    {"id": "js_dependency", "title": "Conteúdo sem depender de JavaScript", "text": "Se o texto aparece sem JavaScript, que a maioria dos robôs de IA não executa."},
    {"id": "structured_data", "title": "Dados estruturados", "text": "Se o site diz, em código, quem é a empresa, onde fica e o que vende."},
    {"id": "sitemap", "title": "Sitemap", "text": "Se existe um índice das páginas que os robôs conseguem encontrar."},
    {"id": "basics", "title": "Básicos do site", "text": "HTTPS, velocidade, título, descrição e idioma declarado."},
]

FAQ = [
    {"q": "É grátis mesmo?", "a": "Sim. O Raio-X IA não cobra pelo relatório. O site se mantém com anúncios e com a nossa consultoria, para quem quiser ajuda para corrigir os problemas."},
    {"q": "Vocês mexem no meu site?", "a": "Não. Só lemos a página inicial e arquivos públicos da raiz (robots.txt, llms.txt, sitemap), como qualquer visitante. Nada é alterado."},
    {"q": "Por que pedem o meu e-mail?", "a": "Para enviar o relatório e confirmar que o pedido é de verdade. A análise só começa depois do clique no link de confirmação."},
    {"q": "Bloquear os robôs de treino de IA é ruim?", "a": "Não necessariamente. Bloquear o treino é uma escolha legítima e não tira pontos. O que tira o seu site das respostas é bloquear os robôs de busca e de respostas."},
    {"q": "O resultado do bloqueio na CDN é definitivo?", "a": "É indicativo. Nossas requisições não vêm dos endereços oficiais dos robôs, e alguns firewalls tratam o robô verdadeiro de forma diferente. Mostramos como conferir no painel."},
]
