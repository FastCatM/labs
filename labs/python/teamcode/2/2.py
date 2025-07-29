def legngth_of_last_word(s:str):
    l = s.split()
    return len(l[-1])


s=input()

print(legngth_of_last_word(s))
