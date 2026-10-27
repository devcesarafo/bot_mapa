# Bot de Hunt – GUIA de uso (v4, 26/10/2026)

*Complementa o documento de passagem v4. Aqui: como rodar, o que cada linha do log significa, onde mexer nas constantes e como testar sem o jogo.*

## 1. Como rodar (terminal como ADMINISTRADOR, jogo na janela principal 1920×1080)

| Comando | O que faz |
| --- | --- |
| `python bot_fase1.py --seco` | Só loga o que apertaria (não envia tecla, não mexe no mouse). Use primeiro depois de qualquer mudança. |
| `python bot_fase1.py` | G, F, C, R em rajada e revive `e`. |
| `python bot_fase1.py --sem-revive` | Igual, mas o revive fica com você. |
| `python bot_fase1.py --ditto` | + fase 4: o bot **move o seu mouse** e aperta `o`/`p`. Não use o mouse durante o lure. |
| `python bot_fase1.py --seco --ditto` | Loga os P e os ajustes do Ditto sem mexer no mouse. |
| `python bot_fase1.py --lures caminho\lures.json` | Escolhe o `lures.json` (padrão: o mais recente de `gravacoes\`). |
| `python bot_fase1.py --diagnostico [print.png]` | Mostra o que cada leitor enxerga (Battle list, coordenada, skills, caveiras). Não aperta nada. |
| `python analisar_ditto.py quadros\AAAA-MM-DD_HH-MM-SS` | Analisa uma gravação do `gravador_quadros.py` e gera `analise_ditto.json`. |

**Parada de emergência: Shift+F** (solta todas as teclas e encerra). Ctrl+C também encerra.
Atalhos do bot: F=`f`, G=`g`, C=`c`, R=`r`, revive=`e`, mover=`o`, parar=`p`.
Log em tela e em `monitor_logs\bot_AAAA-MM-DD_HH-MM-SS.txt`. Dependências: `pip install opencv-python numpy pillow mss pynput`.

## 2. O que cada linha do log significa

| Linha | Significado |
| --- | --- |
| `[tecla] G/F/C ... tentativa n/2` e `[ok] ... confirmado pela barra` | Tecla enviada e confirmada pelo ícone escurecendo. Sem confirmação em 0,7 s repete 1×; depois `[FALHOU]` e trava a skill por 6 s. |
| `[R] rajada de 3: ...` | Início de uma rajada de R (3 apertos). O motivo e `[monstros: N na lista, M localizados na tela]` vêm junto. |
| `[ok] R disparou o macro: F3 ...` | Uma skill de área entrou em recarga: o macro rodou. |
| `[R] rajada cancelada ... skills de área esgotadas` | F2, F3 e F4 em recarga: o R não adianta, só um revive reseta. |
| `[R] NÃO apertado (emergência/fora do alcance ...)` | O monitor sugeriu R, mas o bot só aperta com o personagem parado e todos no alcance. |
| `[R] NÃO apertado: só N monstro(s)` | Menos de 3 vivos: provável começo do próximo lure. |
| `[R] aguardando: ...` | R segurado: Ditto em movimento / acabou de parar / esperando ver o Ditto / Ditto não localizado com monstro fora do alcance. |
| `⚠ EMERGÊNCIA: Ditto X% (previsto Y% em 2 s) ...` | R de emergência do bot: a vida do Ditto está acabando. Sobram os monstros fora do alcance. |
| `[REVIVE] aguardando ...` | Skills de área esgotadas, mas há monstro sem stun: não revive. |
| `[tecla] REVIVE ('e') ... resetar / cadência / crítico` | Revive: sem monstros, ou todos stunados com stun sobrando. |
| `[PERIGO] ... FUJA VOCÊ` + beep | Ditto fora e monstro sem stun. **O bot não foge sozinho.** |
| `[revive] sem alarme ...` | O revive foi apertado com todos stunados; o stun ainda dura. |
| `[ditto] Ditto voltou do revive (N monstro(s))` | Início do ciclo do Ditto (só com ≤ 2 monstros). |
| `[ditto] P ('p'): ...` | P enviado (depois do revive, depois de um ajuste ou na parada). |
| `[ditto] personagem passou do limite ...` | O Ditto saiu da visão: esperando o teleporte, sem `o` até lá. |
| `[ditto] teleporte visto / NÃO visto (assumido)` | Teleporte confirmado pela tela, ou assumido depois de 1,2 s. |
| `[ditto] alvo do O: ...` | Tile escolhido para o mouse (lure, ponto da rota, deslocamento, abertura). |
| `[ditto] antecipação: ...` | Checagem logo depois da parada: a área comporta ou não a lista. |
| `[ditto] AJUSTE (n/4): MERGULHO / RETIRADA ...` | Ajuste do Ditto com a cobertura antes e depois. |
| `[ditto] ... sem ajuste: ...` | Monstro parado fora do alcance e nenhuma solução segura. Resolva à mão. |
| `[ERRO] ...` | Erro de leitura em um quadro: nenhuma tecla é apertada nele (máx. 1 por 2 s). |
| `[aviso] só N leituras/s` | Abaixo de `FPS_MIN` (2,0) o bot não aperta nada. |

## 3. Constantes (valor padrão; mexer só uma de cada vez e testar com `--seco`)

**bot_fase1.py**

| Constante | Valor | Efeito |
| --- | --- | --- |
| `FPS_MIN` | 2,0 | Leituras/s mínimas para agir. |
| `CONFIRMA_S` / `MAX_TENTATIVAS` / `TRAVA_S` | 0,7 s / 2 / 6 s | Confirmação das teclas G, F, C. |
| `LEITURAS_SEGUIDAS` | 2 | Quantas leituras a decisão precisa se repetir (o R de emergência usa 1). |
| `CURA_PERIGO_HP` | 0,40 | Abaixo disso o C passa na frente e não espera o R. |
| `CURA_ESPERA_R_S` | 1,5 s | Quanto o C pode esperar o R entre 40% e 45%. |
| `R_POR_RAJADA` / `R_INTERVALO_S` / `R_PAUSA_S` | 3 / 0,25 s / 1,0 s | Rajada de R. |
| `R_MIN_MONSTROS` | 3 | Menos que isso o bot não aperta R (1 ou 2 = próximo lure). |
| `R_MAX_LURE` | 12 | Máximo de R por lure. |
| `R_APOS_C_S` | 1,0 s | R segurado depois de um C. |
| `HUMANIZAR` | True | Varia o intervalo do R em ±30%. |
| `R_EMERG_DITTO` | True | Liga o R de emergência do bot. |
| `R_EMERG_HP` / `R_EMERG_PARADO_S` | 0,35 / 2,0 s | Vida do Ditto e tempo parado para o R de emergência (além da tendência de queda). |
| `REVIVE_SEM_MONSTROS_S` | 0,6 s | Sem monstros há isso (2 leituras) para o revive de reset. |
| `REVIVE_STUN_MIN_S` | 3,0 s | Stun restante mínimo para reviver com monstros vivos. |
| `DITTO_CRITICO_HP` | 0,25 | Revive crítico (C em recarga, todos stunados). |
| `REVIVE_ENTRE_S` / `REVIVE_MAX_LURE` | 6 s / 3 | Intervalo entre revives e máximo com monstros vivos. |

**ditto_ctl.py**

| Constante | Valor | Efeito |
| --- | --- | --- |
| `O_INTERVALO_S` | 0,21 s | Intervalo do `o` (~4,5 Hz). |
| `P_APOS_REVIVE_S` / `ESPERA_P_MAX_S` | 0,8 s / 5 s | P depois do revive, enquanto o personagem sai. |
| `SAI_X`, `SAI_Y` | 10, 5 | Passou de 9 horizontal ou 4 vertical: Ditto fora da visão. |
| `TELEPORTE_MIN_S` / `TELEPORTE_MAX_S` | 0,15 / 1,2 s | Espera do teleporte (depois assume). |
| `RAIO_PERTO_TILES` | 2,6 | Distância em que o Ditto "visto" conta como teleportado. |
| `PARADO_S` / `O_APOS_PARAR_S` / `P_APOS_PARAR_S` | 0,35 / 0,4 / 1,6 s | Personagem parado, O extra e P final. |
| `CICLO_MAX_MON` | 2 | Revive com mais que isso é de luta: não mexe no Ditto. |
| `REPOS_MAX_CICLO` / `REPOS_ENTRE_S` | 4 / 2,5 s | Ajustes por lure e intervalo. |
| `REPOS_VEL_TILES_S` | 4,0 | Velocidade assumida do Ditto para o tempo dos O. |
| `SETTLE_P_S` / `VER_MAX_S` | 0,5 / 2,0 s | Espera do R depois de um P / depois de um ajuste. |

**alvo_ditto.py**

| Constante | Valor | Efeito |
| --- | --- | --- |
| `JANELA_X`, `JANELA_Y` | 10, 5 | Janela em tiles em que o cursor pode cair. |
| `FRENTE_IDEAL` | 10 | Distância preferida do alvo do O (o mais longe). |
| `OLHAR_PONTOS` | 12 | Pontos da rota que definem o sentido. |
| `RAIO_PARADA` | 3 | Raio em volta da parada para o alvo. |
| `ETA_S_TILE` / `ETA_MAX_S` | 0,5 s / 6 s | Só usados no diagnóstico `problemas()`/`alcance()`. |

**monitor_combate.py (Cerebro)**

| Constante | Valor | Efeito |
| --- | --- | --- |
| `CURA_HP` | 0,45 | Vida do Ditto para o C. |
| `R_PARADO_MIN_S` / `R_PARADO_MAX_S` | 2,5 / 5,0 s | Parado mínimo para o R e espera máxima. |
| `R_ESTAVEL_S` | 1,0 s | Nº de monstros estável por isso. |
| `R_EMERGENCIA_HP` | 0,55 | Emergência do monitor (só recomendação; o bot usa a regra própria). |
| `LURE_MAX_S` | 20 s | Lure longo = emergência. |
| `STUN_DURACAO_S` | 6,2 s | Duração do stun (medida). |
| `REVIVE_STUN_MIN_S` | 2,5 s | Stun mínimo do card (o bot usa 3,0 s). |
| `DITTO_MEMORIA_S` | 1,5 s | Memória da posição do Ditto. |

## 4. Como testar uma mudança sem abrir o jogo (teste de fumaça)

1. Substitua `mss` e `pynput` por falsos (`mss.mss()` devolve um objeto que entrega um **print seu** como quadro; `pynput.keyboard.Controller` grava as teclas numa lista).
2. Faça `Cerebro.processar` levantar `KeyboardInterrupt` depois de ~40 quadros.
3. Chame `bot_fase1.main(seco=True, lures=..., ditto=True)` e confira que não aparece `[ERRO]` nem `Traceback`.
4. Rode `python -m pyflakes bot_fase1.py ditto_ctl.py alvo_ditto.py monitor_combate.py`: só devem sobrar avisos antigos (`threading` repetido, `CURA_HP` não usado, variável `e`).

Esse teste teria pego o erro de 24/10 (variável `ctl` sobrescrita) antes de você abrir o jogo. Ele **não** prova que o comportamento no jogo está certo: isso só o log de um lure real mostra.

## 5. O que mandar para mim quando algo der errado

- O **log completo** do lure (de `monitor_logs\bot_*.txt`) e, se possível, **um print** do instante (com a Battle list visível).
- As linhas `[ditto] AJUSTE`, `⚠ EMERGÊNCIA`, `[PERIGO]`, `[ERRO]` e `[R] NÃO apertado`.
- O que **você** fez à mão no meio (R, revive, fuga), com o horário aproximado.
