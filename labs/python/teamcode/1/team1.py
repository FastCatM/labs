l=['/----------\\']
for i in range(0,int(input())):
    s='\\*!'+input()+"!*/"
    l.append(s)
l.append('/----------\\')
print(*l,sep='\n')