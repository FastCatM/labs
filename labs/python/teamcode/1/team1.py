l=[]
countn=int(input())
for i in range(1,countn+1):
    l.append(input())
print('/----------\\')
for i in l:
    print('\\*!',i,"!*/",sep="")
print('/----------\\')