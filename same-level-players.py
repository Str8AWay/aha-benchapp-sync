import bs4
import requests

def same_level_player(player_number):
    response = requests.get("https://www.ahahockey.com/player/" + str(player_number))
    soup = bs4.BeautifulSoup(response.text, 'html.parser')
    levels = []
    for team in soup.select('.player-stats .current .team-info .content h4'):
        level = team.get_text().split(' ')[0]
        if level not in levels:
            levels.append(level)
        else:
            return (True, True)
    return (bool(levels), False)

active_players = 0
same_level_players = 0
for player_number in range(10000,23000):
    (active, same_level) = same_level_player(player_number)
    if active:
        active_players += 1
    if same_level:
        same_level_players += 1
    if player_number % 1000 == 0:
        print(player_number)
print("Active Players: " + str(active_players))
print("Same Level: " + str(same_level_players))
print("Percentage: " + str(float(same_level_players)/active_players))
