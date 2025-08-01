print("please open this link ",'https://docs.google.com/document/d/1EUuUt5mE8DKNwNwRCiunm9IVIZSIClAHYCkPrla47Ng/edit?tab=t.0')
right_ansfers=['tralalero tralala', 'bombordiro crocodilo', 'lirilirilarila' ,'frulifrula']
correct=0
for i in range(0,len(right_ansfers)):
    s='picture number : '+str(i+1)+ ' '
    brainrooot = input(s)
    if brainrooot==right_ansfers[i]:
        correct+=1
        print('correct!')
    else:
        print('incorrect')
print('your score: ',correct)

